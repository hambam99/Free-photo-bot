import os
import logging
import urllib.parse
import asyncio
import io
import httpx
import random
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
        "Generate HD photos and real AI videos with zero limits!\n\n"
        "**Available Commands:**\n"
        "📸 `/photo <prompt>` — Generate HD photo\n"
        "🎬 `/video <prompt>` — Generate AI video clip\n\n"
        "**Examples:**\n"
        "`/photo futuristic cyberpunk city at sunset, 8k render`\n"
        "`/video airplane flying fast through pink clouds`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Rendering image...*", parse_mode='Markdown')

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
        except Exception as primary_error:
            logging.warning(f"Primary image model failed, trying backup: {primary_error}")

        if not image_bytes:
            try:
                response = await client.get(backup_url, headers=headers)
                if response.status_code == 200:
                    image_bytes = response.content
            except Exception as backup_error:
                logging.error(f"Backup image model failed: {backup_error}")

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
                caption="📁 *Full Quality File*",
                parse_mode='Markdown'
            )
            await status_msg.delete()
        except Exception as e:
            logging.error(f"Telegram photo send error: {e}")
            await status_msg.edit_text("❌ Error sending image to chat.")
    else:
        await status_msg.edit_text("❌ Image server is busy. Please try again in a moment.")

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/video airplane flying through clouds`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎬 *Rendering real motion video (takes 30-60s)...*", parse_mode='Markdown')

    try:
        encoded_prompt = urllib.parse.quote(f"{user_prompt}, smooth camera movement, cinematic motion, video")
        seed = random.randint(1000, 999999)
        
        # Native direct video endpoint
        video_url = f"https://gen.pollinations.ai/video/{encoded_prompt}?seed={seed}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8"
        }

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(video_url, headers=headers)
            if response.status_code != 200 or len(response.content) < 10000:
                # Fallback video endpoint
                alt_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=video&seed={seed}&nologo=true"
                response = await client.get(alt_url, headers=headers)
            
            video_bytes = response.content

        vid_file = io.BytesIO(video_bytes)
        vid_file.name = f"video_{seed}.mp4"

        await update.message.reply_video(
            video=vid_file,
            caption=f"🎬 *AI Video:* `{user_prompt}`",
            parse_mode='Markdown',
            supports_streaming=True
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Video generation error: {e}")
        await status_msg.edit_text("❌ Video generation timed out. Free AI video servers are currently loaded, please try again in a few moments!")

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
