import os
import logging
import urllib.parse
import asyncio
import io
import httpx
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
        "Generate full-resolution uncompressed images!\n\n"
        "**Usage:**\n"
        "`/photo <your prompt>`\n\n"
        "**Example:**\n"
        "`/photo futuristic cyberpunk city, 8k, photorealistic, highly detailed`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Rendering ultra-high-resolution image...*", parse_mode='Markdown')

    try:
        encoded_prompt = urllib.parse.quote(prompt)
        
        # High quality FLUX model parameters (1600x1600 resolution)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1600&height=1600&model=flux&nologo=true&enhance=true"

        # Download raw bytes to bypass Telegram photo compression
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(image_url)
            if response.status_code != 200:
                raise Exception("Failed to download image from generator service.")
            
            image_bytes = io.BytesIO(response.content)
            image_bytes.name = "generated_image_4k.png"

        # Send as uncompressed document to preserve 100% original quality
        await update.message.reply_document(
            document=image_bytes,
            filename="hd_image.png",
            caption=f"✨ *Prompt:* `{prompt}`\n💎 *Quality:* Uncompressed PNG",
            parse_mode='Markdown'
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Generation error: {e}")
        await status_msg.edit_text("❌ Failed to generate the photo. Please try again with a descriptive prompt.")

async def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=60.0,
        pool_timeout=30.0
    )

    app = ApplicationBuilder().token(BOT_TOKEN).request(request_kwargs).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("photo", photo_command))

    config = Config()
    port = int(os.environ.get("PORT", 10000))
    config.bind = [f"0.0.0.0:{port}"]

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    asyncio.create_task(serve(quart_app, config))
    
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())
