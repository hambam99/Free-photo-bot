import os
import logging
import urllib.parse
import threading
import httpx
import random
from quart import Quart
from hypercorn.config import Config
from hypercorn.asyncio import serve
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# Configure logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Web server for Render health checks
quart_app = Quart(__name__)

@quart_app.route('/')
async def home():
    return "Free AI Photo Generator Bot is Online!", 200

def run_web_server():
    """Runs Hypercorn web server in a background thread."""
    config = Config()
    port = int(os.environ.get("PORT", 10000))
    config.bind = [f"0.0.0.0:{port}"]
    import asyncio
    asyncio.run(serve(quart_app, config))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎨 **100% Free HD AI Photo Generator**\n\n"
        "Generate stunning AI images with zero limits!\n\n"
        "**Usage:**\n"
        "`/photo <your prompt>`\n\n"
        "**Example:**\n"
        "`/photo futuristic cyberpunk city, neon lights, masterpiece`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_prompt = " ".join(context.args)
    if not user_prompt:
        await update.message.reply_text(
            "❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat on Mars`", 
            parse_mode='Markdown'
        )
        return

    status_msg = await update.message.reply_text("🎨 *Rendering high-detail image...*", parse_mode='Markdown')

    try:
        # Quality-enhanced prompt engineering
        quality_tags = "masterpiece, ultra detailed, 8k resolution, cinematic lighting, photorealistic"
        enhanced_prompt = f"{user_prompt}, {quality_tags}"
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        
        seed = random.randint(1, 999999)
        
        # Native FLUX high-quality endpoint
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&seed={seed}&nologo=true"

        # Download raw bytes
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            response = await client.get(image_url)
            if response.status_code != 200:
                raise Exception(f"HTTP Error {response.status_code}")
            image_bytes = response.content

        # Send photo to user
        await update.message.reply_photo(
            photo=image_bytes,
            caption=f"✨ *Prompt:* `{user_prompt}`",
            parse_mode='Markdown'
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Generation error: {e}")
        await status_msg.edit_text("❌ Generation timed out or failed. Please try again with a different prompt.")

def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    # Start background web server thread for Render health checks
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()

    # Network timeout configuration
    request_kwargs = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=30.0
    )

    # Build and start Telegram bot application
    app = ApplicationBuilder().token(BOT_TOKEN).request(request_kwargs).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("photo", photo_command))

    logging.info("Starting Telegram Bot Polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
