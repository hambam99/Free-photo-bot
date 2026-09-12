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
    return "Free AI Photo Generator Bot is Online!", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎨 **100% Free Ultra-HD AI Photo Generator**\n\n"
        "Generate maximum resolution images with no daily limits!\n\n"
        "**Usage:**\n"
        "`/photo <your prompt>`\n\n"
        "**Example:**\n"
        "`/photo futuristic cyberpunk city at night, cinematic lighting`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Rendering high-detail image...*", parse_mode='Markdown')

    try:
        # Prompt enhancement to trigger high-detail FLUX generation
        full_prompt = f"{user_prompt}, 8k resolution, highly detailed, photorealistic, sharp focus"
        encoded_prompt = urllib.parse.quote(full_prompt)
        
        # Unique seed prevents returning low-res cached images
        seed = random.randint(1000, 999999)
        
        # Native 1024x1024 FLUX high-resolution rendering endpoint
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&seed={seed}&model=flux&nologo=true"

        # Download raw bytes with a browser User-Agent to prevent server-side compression
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            response = await client.get(image_url, headers=headers)
            if response.status_code != 200:
                raise Exception("Service failed to fetch image.")
            
            image_bytes = response.content

        # 1. Send photo preview in Telegram
        await update.message.reply_photo(
            photo=image_bytes,
            caption=f"✨ *Prompt:* `{user_prompt}`",
            parse_mode='Markdown'
        )
        
        # 2. Send uncompressed PNG document file so Telegram cannot compress quality
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
        logging.error(f"Generation error: {e}")
        await status_msg.edit_text("❌ Generation timed out or failed. Please try again with a different prompt.")

async def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    # Extended request timeouts to eliminate timeout drop errors
    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0
    )

    app = ApplicationBuilder().token(BOT_TOKEN).request(request_kwargs).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("photo", photo_command))

    # Initialize bot framework
    await app.initialize()
    await app.start()

    # Clear lingering polling connections before starting new polling loop
    await app.updater.start_polling(drop_pending_updates=True)

    config = Config()
    port = int(os.environ.get("PORT", 10000))
    config.bind = [f"0.0.0.0:{port}"]

    # Run Quart web app for Render health checks
    await serve(quart_app, config)

if __name__ == '__main__':
    asyncio.run(main())
