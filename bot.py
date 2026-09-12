import os
import logging
import urllib.parse
import asyncio
from quart import Quart
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from hypercorn.config import Config
from hypercorn.asyncio import serve

# Configure logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Web server for Render health checks
quart_app = Quart(__name__)

@quart_app.route('/')
async def home():
    return "Free AI Photo Generator Bot is Online!", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎨 **100% Free AI Photo Generator**\n\n"
        "Generate images with no daily limits or API keys required!\n\n"
        "**Usage:**\n"
        "`/photo <your prompt>`\n\n"
        "**Example:**\n"
        "`/photo a futuristic city at sunset, highly detailed, 8k render`"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')

async def photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("❌ Please provide a prompt!\nExample: `/photo a cute astronaut cat on the moon`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🎨 *Generating your AI image...*", parse_mode='Markdown')

    try:
        # Encode prompt safely for HTTP URL
        encoded_prompt = urllib.parse.quote(prompt)
        
        # Free FLUX model endpoint via Pollinations.ai
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&model=flux&nologo=true"

        # Send image to Telegram
        await update.message.reply_photo(
            photo=image_url,
            caption=f"✨ *Prompt:* `{prompt}`",
            parse_mode='Markdown'
        )
        await status_msg.delete()

    except Exception as e:
        logging.error(f"Generation error: {e}")
        await status_msg.edit_text("❌ Failed to generate the photo. Please try again with a different prompt.")

async def main():
    if not BOT_TOKEN:
        raise ValueError("CRITICAL ERROR: 'BOT_TOKEN' environment variable is missing!")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("photo", photo_command))

    # Port configuration for Render
    config = Config()
    port = int(os.environ.get("PORT", 10000))
    config.bind = [f"0.0.0.0:{port}"]

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    # Run web server alongside bot engine
    await serve(quart_app, config)

if __name__ == '__main__':
    asyncio.run(main())

