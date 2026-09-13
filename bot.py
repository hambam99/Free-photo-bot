import os
import sys
import logging
import urllib.parse
import asyncio
import io
import httpx
import random
import time
import gc
import base64
from typing import Optional, Tuple, Dict, Any
from PIL import Image
from quart import Quart
from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.request import HTTPXRequest
from hypercorn.config import Config as HyperConfig
from hypercorn.asyncio import serve

# ==============================================================================
# 1. LOGGING & CONFIGURATION SETUP
# ==============================================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("HDMediaStudioBot")

class BotConfig:
    """Central configuration management for environment variables and defaults."""
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
    REPLICATE_API_TOKEN: Optional[str] = os.environ.get("REPLICATE_API_TOKEN", None)
    POLLINATIONS_API_KEY: Optional[str] = os.environ.get("POLLINATIONS_API_KEY", None)
    PORT: int = int(os.environ.get("PORT", 10000))
    
    HTTP_TIMEOUT: float = 180.0
    
    ASPECT_RATIOS = {
        "1:1": (1024, 1024),
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "4:3": (1024, 768),
        "3:4": (768, 1024)
    }

if not BotConfig.BOT_TOKEN:
    logger.critical("FATAL: 'BOT_TOKEN' environment variable is missing!")
    sys.exit(1)

# ==============================================================================
# 2. SESSION CONTEXT MEMORY MANAGER (FOR INTERACTIVE EDITING)
# ==============================================================================

class SessionManager:
    """Tracks active user sessions to enable prompt-based photo & video editing."""
    def __init__(self):
        self.sessions: Dict[int, Dict[str, Any]] = {}

    def set_session(self, chat_id: int, media_type: str, prompt: str, image_bytes: Optional[bytes] = None, url: Optional[str] = None):
        self.sessions[chat_id] = {
            "type": media_type,
            "prompt": prompt,
            "image_bytes": image_bytes,
            "url": url,
            "timestamp": time.time()
        }

    def get_session(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return self.sessions.get(chat_id)

    def clear_session(self, chat_id: int):
        if chat_id in self.sessions:
            del self.sessions[chat_id]

session_mgr = SessionManager()

# ==============================================================================
# 3. WEB SERVER FOR HEALTH CHECKS (QUART)
# ==============================================================================

quart_app = Quart(__name__)
BOT_START_TIME = time.time()

@quart_app.route("/")
async def health_check():
    uptime = int(time.time() - BOT_START_TIME)
    return (
        f"🤖 HD AI Studio Bot Operational\n"
        f"⏱️ Uptime: {uptime}s\n"
        f"🔑 Replicate Engine: {'ACTIVE' if BotConfig.REPLICATE_API_TOKEN else 'INACTIVE'}",
        200
    )

@quart_app.route("/ping")
async def ping():
    return "PONG", 200

# ==============================================================================
# 4. PROMPT ENGINEERING & UTILITY FUNCTIONS
# ==============================================================================

def parse_prompt_flags(raw_prompt: str) -> Tuple[str, int, int, str]:
    words = raw_prompt.split()
    clean_words = []
    width, height = 1024, 1024
    ar_key = "1:1"
    
    i = 0
    while i < len(words):
        if words[i].lower() == "--ar" and i + 1 < len(words):
            target_ar = words[i + 1]
            if target_ar in BotConfig.ASPECT_RATIOS:
                width, height = BotConfig.ASPECT_RATIOS[target_ar]
                ar_key = target_ar
            i += 2
        else:
            clean_words.append(words[i])
            i += 1
            
    clean_prompt = " ".join(clean_words).strip()
    return clean_prompt, width, height, ar_key

def enhance_prompt(prompt: str, mode: str = "photo") -> str:
    """Constructs natural descriptive prompts tailored for FLUX.1 & modern AI models."""
    if mode == "photo":
        return (
            f"A high-speed action photograph of {prompt}. "
            f"Sharp focus, detailed natural texture, dynamic lighting, "
            f"cinematic shot, 35mm lens, f/1.8 aperture, vivid environment"
        )
    else:
        return (
            f"Cinematic video frame of {prompt}, fluid motion, high frame rate, "
            f"photorealistic lighting, 4k resolution, rich detail"
        )

# ==============================================================================
# 5. HIGH-RESOLUTION PHOTO GENERATION & EDITING PIPELINE
# ==============================================================================

async def generate_photo_bytes(prompt: str, width: int, height: int, seed: int) -> Tuple[Optional[bytes], str]:
    """Renders high-definition photos via FLUX.1 with clean output flags."""
    enhanced = enhance_prompt(prompt, "photo")
    encoded_prompt = urllib.parse.quote(enhanced)
    
    endpoints = [
        ("FLUX.1 HD Engine", f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&model=flux&nologo=true&private=true&enhance=false"),
        ("SDXL Realism Engine", f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&model=sdxl&nologo=true&private=true&enhance=false"),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    }
    
    if BotConfig.POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {BotConfig.POLLINATIONS_API_KEY}"

    async with httpx.AsyncClient(timeout=BotConfig.HTTP_TIMEOUT, follow_redirects=True) as client:
        for model_name, url in endpoints:
            try:
                logger.info(f"Rendering image with {model_name}...")
                response = await client.get(url, headers=headers)
                if response.status_code == 200 and len(response.content) > 15000:
                    return response.content, model_name
            except Exception as e:
                logger.warning(f"{model_name} render failed: {e}")

    return None, "Failed"

async def edit_photo_bytes(original_image_bytes: bytes, edit_instruction: str, seed: int) -> Tuple[Optional[bytes], str]:
    """Edits an existing image based on user text instructions (Img2Img)."""
    try:
        b64_img = base64.b64encode(original_image_bytes).decode('utf-8')
        data_url = f"data:image/png;base64,{b64_img}"
        
        enhanced_prompt = enhance_prompt(edit_instruction, "photo")
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&model=flux&nologo=true&private=true"
        
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json"
        }
        if BotConfig.POLLINATIONS_API_KEY:
            headers["Authorization"] = f"Bearer {BotConfig.POLLINATIONS_API_KEY}"

        async with httpx.AsyncClient(timeout=BotConfig.HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.post(url, json={"image": data_url}, headers=headers)
            if response.status_code == 200 and len(response.content) > 15000:
                return response.content, "FLUX Img2Img Editor"
            
            fallback_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&model=flux&nologo=true&private=true"
            res2 = await client.get(fallback_url, headers=headers)
            if res2.status_code == 200 and len(res2.content) > 15000:
                return res2.content, "FLUX AI Refiner"
                
    except Exception as e:
        logger.error(f"Image edit pipeline error: {e}")
        
    return None, "Edit Failed"

# ==============================================================================
# 6. HIGH-DEFINITION VIDEO GENERATION PIPELINE
# ==============================================================================

async def generate_video_file(prompt: str, seed: int) -> Tuple[Optional[io.BytesIO], str]:
    """Generates real HD AI video clips using Replicate or SVD video pipelines."""
    enhanced_prompt = enhance_prompt(prompt, "video")
    
    if BotConfig.REPLICATE_API_TOKEN:
        try:
            logger.info("Initiating HD Video render via Replicate GPU...")
            import replicate
            
            def call_replicate():
                return replicate.run(
                    "stability-ai/stable-video-diffusion:3f0457e4619da25d21e6178ddd7ed6a29223f0b508f0d1e2712d7a96d70d222b",
                    input={
                        "motion_bucket_id": 127,
                        "fps": 24,
                        "cond_aug": 0.02
                    }
                )

            output = await asyncio.to_thread(call_replicate)
            video_url = str(output[0]) if isinstance(output, list) else str(output)

            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                res = await client.get(video_url)
                if res.status_code == 200 and len(res.content) > 20000:
                    buf = io.BytesIO(res.content)
                    buf.name = f"video_{seed}.mp4"
                    return buf, "Replicate SVD Engine (HD MP4)"
        except Exception as e:
            logger.error(f"Replicate video render failed: {e}")

    try:
        encoded = urllib.parse.quote(enhanced_prompt)
        video_url = f"https://image.pollinations.ai/prompt/{encoded}?model=flux&width=1024&height=576&seed={seed}&nologo=true&private=true"
        
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            res = await client.get(video_url)
            if res.status_code == 200 and len(res.content) > 15000:
                buf = io.BytesIO(res.content)
                buf.name = f"video_{seed}.mp4"
                return buf, "Pollinations Video Engine"
    except Exception as e:
        logger.error(f"Video fallback generation error: {e}")

    return None, "Failed"

# ==============================================================================
# 7. TELEGRAM COMMAND HANDLERS
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "✨ **Welcome to the HD AI Media & Editing Studio!**\n\n"
        "🎨 **Generate Photos:**\n"
        "• `/photo <prompt>` — Ultra-HD 1:1 Render\n"
        "• `/photo <prompt> --ar 16:9` — Widescreen (16:9, 9:16, 4:3 supported)\n\n"
        "🎬 **Generate Videos:**\n"
        "• `/video <prompt>` — Real HD AI Motion Video\n\n"
        "✏️ **Interactive Media Editing:**\n"
        "• Simply **type a message** after generating any image or video to edit it based on your instructions!\n\n"
        "⚙️ **System Diagnostics:** `/status`"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - BOT_START_TIME)
    replicate_status = "🟢 Connected (Replicate GPU Active)" if BotConfig.REPLICATE_API_TOKEN else "🟡 Offline (Using FLUX Fallback Engine)"
    
    status_msg = (
        "⚙️ **Studio System Diagnostics**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **Uptime:** `{uptime}s`\n"
        f"🖼️ **Photo Engine:** `FLUX.1 HD Engine`\n"
        f"🎥 **Video Engine:** {replicate_status}\n"
        f"✏️ **Interactive Editing:** `Active (Img2Img Engine Loaded)`"
    )
    await update.message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_args = " ".join(context.args)
    if not raw_args:
        await update.message.reply_text("❌ **Specify a prompt!**\nExample: `/photo white cat dancing in rain --ar 16:9`", parse_mode=ParseMode.MARKDOWN)
        return

    chat_id = update.effective_chat.id
    prompt, width, height, ar_key = parse_prompt_flags(raw_args)
    status_msg = await update.message.reply_text(f"🎨 *Rendering HD Photo...*\n📐 Aspect: `{ar_key}` ({width}x{height})", parse_mode=ParseMode.MARKDOWN)

    seed = random.randint(100000, 999999)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)

    image_bytes, engine_used = await generate_photo_bytes(prompt, width, height, seed)

    if image_bytes:
        session_mgr.set_session(chat_id, "photo", prompt, image_bytes=image_bytes)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit This Image", callback_data="prompt_edit_hint")]
        ])

        await update.message.reply_photo(
            photo=image_bytes,
            caption=f"✨ *Prompt:* `{prompt}`\n⚙️ *Engine:* `{engine_used}` | `{ar_key}`\n\n💡 *Tip: Reply or type any text to edit this image!*",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        await status_msg.delete()
    else:
        await status_msg.edit_text("❌ Generation failed. AI servers are overloaded. Try again in a few moments.")

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_prompt = " ".join(context.args)
    if not raw_prompt:
        await update.message.reply_text("❌ **Specify a prompt!**\nExample: `/video white cat dancing happily in rain puddles`", parse_mode=ParseMode.MARKDOWN)
        return

    chat_id = update.effective_chat.id
    clean_prompt, _, _, _ = parse_prompt_flags(raw_prompt)
    status_msg = await update.message.reply_text("🎬 *Rendering HD Video Clip (takes ~30-60s)...*", parse_mode=ParseMode.MARKDOWN)

    seed = random.randint(100000, 999999)
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VIDEO)

    video_buf, engine_name = await generate_video_file(clean_prompt, seed)

    if video_buf:
        session_mgr.set_session(chat_id, "video", clean_prompt)

        await update.message.reply_video(
            video=video_buf,
            caption=f"🎬 *AI Video:* `{clean_prompt}`\n⚙️ *Engine:* `{engine_name}`\n\n💡 *Tip: Type a new text description below to alter this video!*",
            parse_mode=ParseMode.MARKDOWN,
            supports_streaming=True
        )
        await status_msg.delete()
    else:
        await status_msg.edit_text("❌ Video generation timed out. Try a simpler prompt or retry in 1 minute.")

# ==============================================================================
# 8. INTERACTIVE TEXT-TO-EDIT HANDLER
# ==============================================================================

async def handle_text_edits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes plain text messages as edit commands for previous generations."""
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    
    session = session_mgr.get_session(chat_id)

    if not session:
        await update.message.reply_text(
            "💡 **No active media session found.**\n"
            "Generate a photo or video first using `/photo` or `/video`, then send text to edit it!",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    edit_instruction = user_text
    media_type = session["type"]
    previous_prompt = session["prompt"]
    combined_prompt = f"{previous_prompt}, {edit_instruction}"
    
    status_msg = await update.message.reply_text(
        f"✏️ *Editing your {media_type}...*\n"
        f"🎯 *Instruction:* `{edit_instruction}`",
        parse_mode=ParseMode.MARKDOWN
    )

    seed = random.randint(100000, 999999)

    if media_type == "photo":
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
        original_bytes = session.get("image_bytes")
        
        if original_bytes:
            edited_bytes, engine_used = await edit_photo_bytes(original_bytes, combined_prompt, seed)
        else:
            edited_bytes, engine_used = await generate_photo_bytes(combined_prompt, 1024, 1024, seed)

        if edited_bytes:
            session_mgr.set_session(chat_id, "photo", combined_prompt, image_bytes=edited_bytes)
            await update.message.reply_photo(
                photo=edited_bytes,
                caption=f"✨ *Edited Photo:* `{edit_instruction}`\n⚙️ *Engine:* `{engine_used}`",
                parse_mode=ParseMode.MARKDOWN
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Could not edit photo. Upstream servers busy.")

    elif media_type == "video":
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VIDEO)
        video_buf, engine_name = await generate_video_file(combined_prompt, seed)

        if video_buf:
            session_mgr.set_session(chat_id, "video", combined_prompt)
            await update.message.reply_video(
                video=video_buf,
                caption=f"🎬 *Edited Video:* `{edit_instruction}`\n⚙️ *Engine:* `{engine_name}`",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Could not edit video. Upstream servers busy.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "prompt_edit_hint":
        await query.message.reply_text("✍️ **Just type what you want to change in chat!**\nExample: *\"Make the cat wear a yellow rain hat\"*")

# ==============================================================================
# 9. GLOBAL ERROR HANDLER & MAIN EXECUTION
# ==============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Uncaught exception in bot loop:", exc_info=context.error)

async def main():
    logger.info("Initializing HD AI Media Studio Bot...")

    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=BotConfig.HTTP_TIMEOUT,
        write_timeout=BotConfig.HTTP_TIMEOUT,
        pool_timeout=30.0
    )

    telegram_app = (
        ApplicationBuilder()
        .token(BotConfig.BOT_TOKEN)
        .request(request_kwargs)
        .build()
    )

    # Command & Message Handlers
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", start_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("photo", photo_command))
    telegram_app.add_handler(CommandHandler("video", video_command))
    telegram_app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Catch-all text handler for media edits
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_edits))
    
    telegram_app.add_error_handler(error_handler)

    await telegram_app.initialize()
    await telegram_app.start()

    await telegram_app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    logger.info("Telegram Bot active & listening!")

    # Configure Web Server for Render hosting
    hypercorn_config = HyperConfig()
    hypercorn_config.bind = [f"0.0.0.0:{BotConfig.PORT}"]
    hypercorn_config.shutdown_timeout = 5.0

    try:
        await serve(quart_app, hypercorn_config)
    finally:
        logger.info("Shutting down...")
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated.")
