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
        "🎨 **100% Free HD AI Photo Generator**\n\n"
        "Generate full quality images with no daily limits!\n\n"
        "**Usage:**\n"
        "`/photo <your prompt>`\n\n"
        "**Example:**\n"
        "`/photo futuristic cyberpunk city, photorealistic, 8k resolution`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Rendering high-detail image...*", parse_mode='Markdown')

    try:
        # Append quality tags to force detail rendering
        enhanced_prompt = f"{prompt}, 8k resolution, highly detailed, photorealistic, cinematic lighting"
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        
        # Add a random seed to prevent cached low-res returns
        seed = random.randint(1, 99999999)
        
        image_url = (
            f"https://pollinations.ai/p/{encoded_prompt}"
            f"?width=1920&height=1080&seed={seed}&model=flux&nologo=true"
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(image_url, headers=headers)
            if response.status_code != 200:
                raise Exception("Failed to fetch rendered image.")
            
            image_bytes = io.BytesIO(response.content)
            image_bytes.name = "hd_image.png"

        # Send as document to prevent Telegram photo downscaling
        await update.message.reply_document(
            document=image_bytes,
            filename="hd_image.png",
            caption=f"✨ *Prompt:* `{prompt}`",
            parse_mode='Markdown'
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Generation error: {e}")
        await status_msg.edit_text("❌ Failed to generate photo. Please try again with a different prompt.")

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
