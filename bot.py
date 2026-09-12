import os
import logging
import urllib.parse
import asyncio
import io
import httpx
import random
import replicate
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
    return "Free AI Photo & Real Video Bot is Online!", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎨 **AI Photo & Video Generator**\n\n"
        "**Available Commands:**\n"
        "📸 `/photo <prompt>` — Free Ultra-HD photo render\n"
        "🎬 `/video <prompt>` — True AI MP4 motion video\n\n"
        "**Examples:**\n"
        "`/photo futuristic cyberpunk city at sunset, 8k`\n"
        "`/video airplane flying fast through pink clouds`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

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
            logging.warning(f"Primary model error, using fallback: {e}")

        if not image_bytes:
            try:
                response = await client.get(backup_url, headers=headers)
                if response.status_code == 200:
                    image_bytes = response.content
            except Exception as e:
                logging.error(f"Backup model error: {e}")

    if image_bytes:
        try:
            await update.message.reply_photo(
                photo=image_bytes,
                caption=f"✨ *Prompt:* `{user_prompt}`",
                parse_mode='Markdown'
            )
            
            doc_file = io.BytesIO(image_bytes)
            doc_file.name = f"render_{seed}.png"
            await update.message.reply_document(
                document=doc_file,
                filename=f"HD_Image_{seed}.png",
                caption="📁 *Full Uncompressed HD File*",
                parse_mode='Markdown'
            )
            await status_msg.delete()
        except Exception as e:
            logging.error(f"Telegram photo error: {e}")
            await status_msg.edit_text("❌ Error sending photo.")
    else:
        await status_msg.edit_text("❌ Server busy. Try again in a moment!")

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/video airplane flying through sunset clouds`", parse_mode='Markdown')
        return

    if not os.environ.get("REPLICATE_API_TOKEN"):
        await update.message.reply_text("⚠️ `REPLICATE_API_TOKEN` environment variable is missing in Render settings.")
        return

    status_msg = await update.message.reply_text("🎬 *Generating real AI motion video (takes ~30-60s)...*", parse_mode='Markdown')

    try:
        # Calls AnimateDiff AI model to generate true 3D frame-by-frame motion
        def run_video_gen():
            return replicate.run(
                "lucataco/animate-diff:beecf59c4aee8099616d7a468d6ff0b115ebfb56cf6d4217112c3f80c65ba8f8",
                input={"prompt": f"{user_prompt}, cinematic motion, highly detailed, photorealistic"}
            )

        video_output = await asyncio.to_thread(run_video_gen)

        # Retrieve video stream URL
        video_url = str(video_output) if isinstance(video_output, str) else str(video_output[0])

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            video_response = await client.get(video_url)
            video_bytes = video_response.content

        vid_file = io.BytesIO(video_bytes)
        vid_file.name = "ai_video.mp4"

        await update.message.reply_video(
            video=vid_file,
            caption=f"🎬 *AI Motion Video:* `{user_prompt}`",
            parse_mode='Markdown',
            supports_streaming=True
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Replicate Video Generation Error: {e}")
        await status_msg.edit_text("❌ Video generation failed. Check your Replicate API token or try a simpler prompt!")

async def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=120.0,
        write_timeout=120.0,
        pool_timeout=30.0
    )

    app = ApplicationBuilder().token(BOT_TOKEN).request(request_kwargs).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("photo", photo_command))
    app.add_handler(CommandHandler("video", video_command))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    config = Config()
    port = int(os.environ.get("PORT", 10000))
    config.bind = [f"0.0.0.0:{port}"]

    await serve(quart_app, config)

if __name__ == '__main__':
    asyncio.run(main())
