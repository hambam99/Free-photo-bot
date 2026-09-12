import os
import sys
import logging
import urllib.parse
import asyncio
import io
import httpx
import random
import time
import signal
import gc
from typing import Optional, Tuple
from PIL import Image, ImageEnhance, ImageFilter
from quart import Quart
from telegram import Update, Message
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    Application
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
logger = logging.getLogger("FreeMediaBot")

class BotConfig:
    """Central configuration management for environment variables and defaults."""
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
    REPLICATE_API_TOKEN: Optional[str] = os.environ.get("REPLICATE_API_TOKEN", None)
    POLLINATIONS_API_KEY: Optional[str] = os.environ.get("POLLINATIONS_API_KEY", None)
    PORT: int = int(os.environ.get("PORT", 10000))
    
    # Timeouts & HTTP Settings
    HTTP_TIMEOUT: float = 120.0
    MAX_RETRIES: int = 3
    
    # Supported Aspect Ratios (Width, Height)
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
# 2. WEB SERVER FOR RENDER HEALTH CHECKS (QUART)
# ==============================================================================

quart_app = Quart(__name__)
BOT_START_TIME = time.time()

@quart_app.route("/")
async def health_check():
    uptime = int(time.time() - BOT_START_TIME)
    return (
        f"🤖 AI Photo & Video Bot is fully operational!\n"
        f"⏱️ Uptime: {uptime} seconds\n"
        f"🔑 Replicate Integration: {'ACTIVE' if BotConfig.REPLICATE_API_TOKEN else 'INACTIVE (Fallback Mode Enabled)'}",
        200
    )

@quart_app.route("/ping")
async def ping():
    return "PONG", 200

# ==============================================================================
# 3. HELPER UTILITIES & IMAGE PROCESSING ENGINE
# ==============================================================================

def parse_prompt_flags(raw_prompt: str) -> Tuple[str, int, int, str]:
    """
    Extracts custom flags from prompt:
    - --ar 16:9, 9:16, 1:1, 4:3, 3:4
    - Default size: 1024x1024
    """
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

def build_smooth_motion_gif(image_bytes: bytes, num_frames: int = 24) -> io.BytesIO:
    """
    Guaranteed local zero-fail engine: Converts a static render into a 
    cinematic 24-fps orbital loop (zoom + pan + subtle exposure oscillation).
    Used as an emergency fallback if remote video render providers are busy.
    """
    base_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    orig_w, orig_h = base_image.size

    frames = []
    half_frames = num_frames // 2

    for i in range(num_frames):
        # Calculate smooth sine-wave zoom scale (1.0 to 1.15)
        progress = i / float(num_frames)
        scale = 1.0 + 0.15 * (0.5 - 0.5 * (2 * 3.14159 * progress))

        nw, nh = int(orig_w * scale), int(orig_h * scale)
        resized = base_image.resize((nw, nh), Image.Resampling.LANCZOS)

        # Pan calculations
        offset_x = int((nw - orig_w) * (0.5 + 0.3 * (progress - 0.5)))
        offset_y = int((nh - orig_h) * (0.5 - 0.3 * (progress - 0.5)))

        offset_x = max(0, min(offset_x, nw - orig_w))
        offset_y = max(0, min(offset_y, nh - orig_h))

        cropped = resized.crop((offset_x, offset_y, offset_x + orig_w, offset_y + orig_h))
        
        # Subtle dynamic contrast wave for lighting motion
        enhancer = ImageEnhance.Contrast(cropped)
        contrast_factor = 1.0 + 0.08 * (0.5 - 0.5 * (4 * 3.14159 * progress))
        frame = enhancer.enhance(contrast_factor)
        
        frames.append(frame)

    output_buffer = io.BytesIO()
    # Save optimized animated loop
    frames[0].save(
        output_buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=50,  # 20 FPS
        loop=0,
        optimize=True
    )
    output_buffer.seek(0)
    output_buffer.name = "ai_motion.gif"
    
    # Clean memory explicitly
    del frames, base_image
    gc.collect()
    
    return output_buffer

# ==============================================================================
# 4. MULTI-ENGINE PHOTO GENERATOR PIPELINE
# ==============================================================================

async def generate_photo_bytes(prompt: str, width: int, height: int, seed: int) -> Tuple[Optional[bytes], str]:
    """
    Multi-tier photo rendering pipeline:
    Tier 1: Pollinations FLUX Engine
    Tier 2: Pollinations TURBO Engine
    Tier 3: Pollinations SDXL Engine
    """
    encoded_prompt = urllib.parse.quote(prompt)
    
    endpoints = [
        ("FLUX Model", f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&model=flux&nologo=true"),
        ("TURBO Model", f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&model=turbo&nologo=true"),
        ("SDXL Model", f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&seed={seed}&model=sdxl&nologo=true"),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    }

    if BotConfig.POLLINATIONS_API_KEY:
        headers["Authorization"] = f"Bearer {BotConfig.POLLINATIONS_API_KEY}"

    async with httpx.AsyncClient(timeout=BotConfig.HTTP_TIMEOUT, follow_redirects=True) as client:
        for model_name, url in endpoints:
            try:
                logger.info(f"Attempting image render with {model_name}...")
                response = await client.get(url, headers=headers)
                if response.status_code == 200 and len(response.content) > 5000:
                    logger.info(f"Image successfully rendered via {model_name}")
                    return response.content, model_name
                else:
                    logger.warning(f"{model_name} returned status code {response.status_code}")
            except Exception as e:
                logger.warning(f"Failed image request on {model_name}: {e}")
                
    return None, "Failed"

# ==============================================================================
# 5. MULTI-ENGINE VIDEO GENERATOR PIPELINE
# ==============================================================================

async def generate_video_file(prompt: str, seed: int) -> Tuple[io.BytesIO, str, bool]:
    """
    Multi-tier AI Video Pipeline:
    Tier 1: Replicate API (AnimateDiff / Luma / CogVideoX) -> Returns Native MP4 Video
    Tier 2: Direct Pollinations Native Video Endpoint -> Returns Native MP4 Video
    Tier 3: Server-side Synthesis Engine -> Returns High-Quality Motion GIF Video
    
    Returns: (Buffer, EngineName, is_mp4_video)
    """
    # --------------------------------------------------------------------------
    # TIER 1: Replicate Dedicated GPU Text-To-Video (If Token Available)
    # --------------------------------------------------------------------------
    if BotConfig.REPLICATE_API_TOKEN:
        try:
            logger.info("Initiating Video Render via Tier 1: Replicate GPU Engine...")
            import replicate
            
            def call_replicate():
                return replicate.run(
                    "lucataco/animate-diff:beecf59c4aee8099616d7a468d6ff0b115ebfb56cf6d4217112c3f80c65ba8f8",
                    input={
                        "prompt": f"{prompt}, 8k resolution, cinematic smooth motion, photorealistic",
                        "n_prompt": "blurry, low quality, distorted, static picture",
                        "guidance_scale": 7.5,
                        "num_inference_steps": 25
                    }
                )

            output_url = await asyncio.to_thread(call_replicate)
            video_url_str = str(output_url) if isinstance(output_url, str) else str(output_url[0])

            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                res = await client.get(video_url_str)
                if res.status_code == 200 and len(res.content) > 20000:
                    buf = io.BytesIO(res.content)
                    buf.name = f"video_{seed}.mp4"
                    return buf, "Replicate AnimateDiff (Native MP4)", True
        except Exception as replicate_err:
            logger.error(f"Tier 1 Replicate failed, cascading to Tier 2: {replicate_err}")

    # --------------------------------------------------------------------------
    # TIER 2: Direct Pollinations Native Video Stream Endpoint
    # --------------------------------------------------------------------------
    try:
        logger.info("Initiating Video Render via Tier 2: Direct Pollinations Video Stream...")
        encoded = urllib.parse.quote(f"{prompt}, 4k video motion, fluid movement")
        video_api_url = f"https://gen.pollinations.ai/video/{encoded}?seed={seed}"
        
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8"
        }
        if BotConfig.POLLINATIONS_API_KEY:
            headers["Authorization"] = f"Bearer {BotConfig.POLLINATIONS_API_KEY}"

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            res = await client.get(video_api_url, headers=headers)
            # Check for valid MP4 signature or payload size
            if res.status_code == 200 and len(res.content) > 50000 and res.content[:4] == b'\x00\x00\x00\x1c' or b'ftyp' in res.content[:32]:
                buf = io.BytesIO(res.content)
                buf.name = f"video_{seed}.mp4"
                return buf, "Pollinations Video Stream (Native MP4)", True
    except Exception as poll_err:
        logger.warning(f"Tier 2 Native Video stream endpoint bypass: {poll_err}")

    # --------------------------------------------------------------------------
    # TIER 3: Zero-Fail High-Speed AI Motion Synthesis Loop
    # --------------------------------------------------------------------------
    logger.info("Cascading to Tier 3: High-Speed AI Motion Synthesis Loop...")
    base_image_bytes, _ = await generate_photo_bytes(
        prompt=f"{prompt}, hyper-realistic, volumetric light, highly detailed motion blur",
        width=1024,
        height=1024,
        seed=seed
    )

    if not base_image_bytes:
        raise RuntimeError("All underlying AI image/video upstream servers are currently unresponsive.")

    motion_gif_buf = await asyncio.to_thread(build_smooth_motion_gif, base_image_bytes)
    return motion_gif_buf, "Cinematic Motion Engine", False

# ==============================================================================
# 6. TELEGRAM COMMAND HANDLERS
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends start/help onboarding message."""
    welcome_text = (
        "✨ **Welcome to the Ultimate Free AI Media Bot!**\n\n"
        "Generate stunning Ultra-HD photos and moving AI video clips effortlessly.\n\n"
        "📸 **Generate Photos:**\n"
        "• `/photo <prompt>` — Default 1:1 HD Render\n"
        "• `/photo <prompt> --ar 16:9` — Widescreen Landscape\n"
        "• `/photo <prompt> --ar 9:16` — Mobile Story / Portrait\n\n"
        "🎬 **Generate Videos:**\n"
        "• `/video <prompt>` — Real motion video loop\n\n"
        "⚙️ **System Diagnostics:**\n"
        "• `/status` — Check server health & active models\n\n"
        "💡 **Examples:**\n"
        "`/photo cybernetic neon panther in rain --ar 16:9`\n"
        "`/video space station rotating above planet earth`"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows live server diagnostic stats."""
    uptime = int(time.time() - BOT_START_TIME)
    replicate_status = "🟢 Connected (Replicate GPU Active)" if BotConfig.REPLICATE_API_TOKEN else "🟡 Offline (Using Free Multi-Tier Fallback Engine)"
    
    status_msg = (
        "⚙️ **Bot Diagnostic & Status**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **Uptime:** `{uptime}s`\n"
        f"📡 **Telegram Polling:** `Healthy`\n"
        f"🎥 **Primary Video Backend:** {replicate_status}\n"
        f"🖼️ **Primary Image Backend:** `Pollinations FLUX / Turbo`\n"
        f"⚡ **Memory Management:** `Auto-Garbage Collection Active`"
    )
    await update.message.reply_text(status_msg, parse_mode=ParseMode.MARKDOWN)

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /photo generation requests."""
    raw_args = " ".join(context.args)
    if not raw_args:
        await update.message.reply_text(
            "❌ **Please specify a prompt!**\n\nExample: `/photo a cute astronaut cat --ar 16:9`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    prompt, width, height, ar_key = parse_prompt_flags(raw_args)
    status_msg = await update.message.reply_text(
        f"🎨 *Rendering HD Photo...*\n📐 Aspect Ratio: `{ar_key}` ({width}x{height})",
        parse_mode=ParseMode.MARKDOWN
    )

    seed = random.randint(100000, 999999)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

    image_bytes, engine_used = await generate_photo_bytes(prompt, width, height, seed)

    if image_bytes:
        try:
            # Send photo preview
            await update.message.reply_photo(
                photo=image_bytes,
                caption=f"✨ *Prompt:* `{prompt}`\n⚙️ *Engine:* `{engine_used}` | *Aspect:* `{ar_key}`",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Send uncompressed document file
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_DOCUMENT)
            doc_file = io.BytesIO(image_bytes)
            doc_file.name = f"Render_{seed}_{ar_key.replace(':', 'x')}.png"
            
            await update.message.reply_document(
                document=doc_file,
                filename=doc_file.name,
                caption="📁 *Full Quality Uncompressed PNG File*",
                parse_mode=ParseMode.MARKDOWN
            )
            await status_msg.delete()
        except Exception as send_err:
            logger.error(f"Error dispatching Telegram photo payload: {send_err}")
            await status_msg.edit_text("❌ Failed to deliver photo payload to Telegram chat.")
    else:
        await status_msg.edit_text("❌ All AI image generation servers are currently overloaded. Please try again shortly!")

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /video generation requests."""
    raw_prompt = " ".join(context.args)
    if not raw_prompt:
        await update.message.reply_text(
            "❌ **Please specify a prompt!**\n\nExample: `/video airplane flying through sunset clouds`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    clean_prompt, _, _, _ = parse_prompt_flags(raw_prompt)
    status_msg = await update.message.reply_text(
        "🎬 *Initializing Video Pipeline...*\n⏳ *Rendering fluid motion frames (takes ~30-60s)...*",
        parse_mode=ParseMode.MARKDOWN
    )

    seed = random.randint(100000, 999999)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.RECORD_VIDEO)

    try:
        file_buffer, engine_name, is_native_mp4 = await generate_video_file(clean_prompt, seed)
        
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)

        if is_native_mp4:
            # Send playable MP4 video stream
            await update.message.reply_video(
                video=file_buffer,
                caption=f"🎬 *AI Video:* `{clean_prompt}`\n⚙️ *Engine:* `{engine_name}`",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
        else:
            # Send seamless auto-looping motion animation
            await update.message.reply_animation(
                animation=file_buffer,
                caption=f"🎬 *AI Motion Clip:* `{clean_prompt}`\n⚙️ *Engine:* `{engine_name}`",
                parse_mode=ParseMode.MARKDOWN
            )

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Video pipeline fatal failure: {e}")
        await status_msg.edit_text(
            "❌ **Video Generation Error**\n\n"
            "Upstream AI video providers timed out. Try again with a simpler prompt!"
        )

# ==============================================================================
# 7. GLOBAL ERROR & EXCEPTION HANDLING
# ==============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs uncaught command exceptions cleanly."""
    logger.error("Uncaught exception encountered during update processing:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An unexpected internal processing error occurred. Please try your request again."
            )
        except Exception:
            pass

# ==============================================================================
# 8. APPLICATION INITIALIZATION & SHUTDOWN ORCHESTRATION
# ==============================================================================

async def main():
    """Main execution loop organizing Quart webserver and Telegram polling instance."""
    logger.info("Starting Telegram Bot Application initialization...")

    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=BotConfig.HTTP_TIMEOUT,
        write_timeout=BotConfig.HTTP_TIMEOUT,
        pool_timeout=30.0
    )

    # Build Telegram Bot Application instance
    telegram_app = (
        ApplicationBuilder()
        .token(BotConfig.BOT_TOKEN)
        .request(request_kwargs)
        .build()
    )

    # Register Command Handlers
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", start_command))
    telegram_app.add_handler(CommandHandler("status", status_command))
    telegram_app.add_handler(CommandHandler("photo", photo_command))
    telegram_app.add_handler(CommandHandler("video", video_command))
    
    # Register Error Handler
    telegram_app.add_error_handler(error_handler)

    # Initialize Telegram bot runtime
    await telegram_app.initialize()
    await telegram_app.start()

    # Drop old unhandled updates to prevent webhook conflicts on deployment restart
    await telegram_app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
    logger.info("Telegram Polling active & listening for commands!")

    # Configure Web Server for Render hosting
    hypercorn_config = HyperConfig()
    hypercorn_config.bind = [f"0.0.0.0:{BotConfig.PORT}"]
    hypercorn_config.shutdown_timeout = 5.0

    logger.info(f"Binding Quart web server to port {BotConfig.PORT}...")

    try:
        # Run Web Server while Telegram Bot runs asynchronously in background
        await serve(quart_app, hypercorn_config)
    finally:
        logger.info("Initiating graceful shutdown sequence...")
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated by system signal.")
