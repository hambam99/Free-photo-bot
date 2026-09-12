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
        "Generate maximum quality images with no API key required!\n\n"
        "**Usage:**\n"
        "`/photo <your prompt>`\n\n"
        "**Example:**\n"
        "`/photo futuristic cyberpunk city at sunset, cinematic lighting, 8k resolution, photorealistic`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat on Mars`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Rendering ultra-high-resolution image...*", parse_mode='Markdown')

    try:
        # Append quality keywords for detailed generation
        full_prompt = f"{user_prompt}, 8k resolution, masterpiece, highly detailed, photorealistic, cinematic lighting"
        encoded_prompt = urllib.parse.quote(full_prompt)
        
        seed = random.randint(10000, 999999)
        
        # High-definition FLUX rendering endpoint
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=2048&height=2048&seed={seed}&model=flux&nologo=true"

        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            response = await client.get(image_url)
            if response.status_code != 200:
                raise Exception("Image generation service failed to return an image.")
            
            image_data = io.BytesIO(response.content)
            image_data.name = "high_res_image.png"

        # Send both photo view AND high-res uncompressed file
        await update.message.reply_photo(
            photo=image_data,
            caption=f"✨ *Prompt:* `{user_prompt}`",
            parse_mode='Markdown'
        )
        
        image_data.seek(0)
        
        await update.message.reply_document(
            document=image_data,
            filename=f"hd_render_{seed}.png",
            caption="📁 *Full Original Uncompressed Quality (2048x2048)*",
            parse_mode='Markdown'
        )
        
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Generation error: {e}")
        await status_msg.edit_text("❌ Failed to generate photo. Please try again with a descriptive prompt.")

async def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    # Initial delay to clear lingering connection instances on Render restart
    await asyncio.sleep(3)

    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
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
