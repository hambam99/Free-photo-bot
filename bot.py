import os
import logging
import urllib.parse
import asyncio
import io
import httpx
import random
from PIL import Image
from quart import Quart
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest
from hypercorn.config import Config
from hypercorn.asyncio import serve

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

quart_app = Quart(__name__)

@quart_app.route('/')
async def home():
    return "Free AI Photo & Video Bot is Online!", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎨 **100% Free AI Media Generator**\n\n"
        "Generate Ultra-HD Photos and AI Motion Videos with zero limits!\n\n"
        "**Available Commands:**\n"
        "📸 `/photo <prompt>` — Generate HD photo + uncompressed file\n"
        "🎬 `/video <prompt>` — Generate AI motion video clip\n\n"
        "**Examples:**\n"
        "`/photo cyberpunk neon samurai at night, 8k render`\n"
        "`/video space shuttle launching into galaxy`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

def generate_motion_clip(image_bytes: bytes) -> io.BytesIO:
    """Creates a 24-frame smooth cinematic motion video loop from the rendered image."""
    base_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = base_img.size

    frames = []
    num_frames = 16  # Frame count for fluid motion

    for i in range(num_frames):
        # Calculate subtle zoom and pan parameters
        scale = 1.0 + (i / num_frames) * 0.12  # Smooth 12% cinematic zoom
        nw, nh = int(width * scale), int(height * scale)

        resized = base_img.resize((nw, nh), Image.Resampling.LANCZOS)

        # Center crop with slight diagonal pan
        pan_offset = int((i / num_frames) * 15)
        left = max(0, (nw - width) // 2 + pan_offset)
        top = max(0, (nh - height) // 2 + pan_offset)
        
        cropped = resized.crop((left, top, left + width, top + height))
        frames.append(cropped)

    # Create ping-pong loop (forward then smooth backward) for seamless infinite motion
    full_loop = frames + frames[::-1][1:-1]

    output_buf = io.BytesIO()
    full_loop[0].save(
        output_buf,
        format='GIF',
        save_all=True,
        append_images=full_loop[1:],
        duration=70,  # ~14 FPS playback speed
        loop=0
    )
    output_buf.seek(0)
    output_buf.name = "motion_clip.gif"
    return output_buf

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Rendering Ultra-HD photo...*", parse_mode='Markdown')

    encoded_prompt = urllib.parse.quote(user_prompt)
    seed = random.randint(1000, 999999)

    primary_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"
    backup_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=turbo&nologo=true"

    headers = {"User-Agent": "Mozilla/5.0"}
    image_bytes = None

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        try:
            response = await client.get(primary_url, headers=headers)
            if response.status_code == 200:
                image_bytes = response.content
        except Exception as e:
            logging.warning(f"Primary FLUX model failed, switching to backup: {e}")

        if not image_bytes:
            try:
                response = await client.get(backup_url, headers=headers)
                if response.status_code == 200:
                    image_bytes = response.content
            except Exception as e:
                logging.error(f"Backup model failed: {e}")

    if image_bytes:
        try:
            # 1. Send preview photo
            await update.message.reply_photo(
                photo=image_bytes,
                caption=f"✨ *Prompt:* `{user_prompt}`",
                parse_mode='Markdown'
            )
            
            # 2. Send raw PNG file attachment (Uncompressed HD)
            doc_file = io.BytesIO(image_bytes)
            doc_file.name = f"render_{seed}.png"
            await update.message.reply_document(
                document=doc_file,
                filename=f"HD_Image_{seed}.png",
                caption="📁 *Full Uncompressed HD Quality File*",
                parse_mode='Markdown'
            )
            await status_msg.delete()
        except Exception as e:
            logging.error(f"Telegram photo dispatch error: {e}")
            await status_msg.edit_text("❌ Error sending photo to chat.")
    else:
        await status_msg.edit_text("❌ Image server is busy. Please try your prompt again in a moment!")

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/video airplane flying through sunset clouds`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎬 *Rendering AI motion video clip...*", parse_mode='Markdown')

    encoded_prompt = urllib.parse.quote(f"{user_prompt}, cinematic motion, volumetric lighting, highly detailed")
    seed = random.randint(1000, 999999)

    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"
    headers = {"User-Agent": "Mozilla/5.0"}
    image_bytes = None

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                image_bytes = response.content
        except Exception as e:
            logging.error(f"Video base frame fetch error: {e}")

    if image_bytes:
        try:
            # Process image bytes into an animated looping video stream
            video_stream = await asyncio.to_thread(generate_motion_clip, image_bytes)

            await update.message.reply_animation(
                animation=video_stream,
                caption=f"🎬 *AI Motion Video:* `{user_prompt}`",
                parse_mode='Markdown'
            )
            await status_msg.delete()
        except Exception as e:
            logging.error(f"Telegram video animation dispatch error: {e}")
            await status_msg.edit_text("❌ Failed to process video animation.")
    else:
        await status_msg.edit_text("❌ Server busy. Please try your video prompt again!")

async def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0
    )

    app = ApplicationBuilder().token(BOT_TOKEN).request(request_kwargs).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("photo", photo_command))
    app.add_handler(CommandHandler("video", video_command))

    await app.initialize()
    await app.start()
    
    # Drops older pending connection instances to eliminate conflict crashes
    await app.updater.start_polling(drop_pending_updates=True)

    config = Config()
    port = int(os.environ.get("PORT", 10000))
    config.bind = [f"0.0.0.0:{port}"]

    await serve(quart_app, config)

if __name__ == '__main__':
    asyncio.run(main())
