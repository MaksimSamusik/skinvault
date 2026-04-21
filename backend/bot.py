"""
Telegram bot entrypoint.
Run: python bot.py
Env vars: BOT_TOKEN, WEBAPP_URL
"""
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN  = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(
        "📦 Открыть SkinVault",
        web_app=WebAppInfo(url=WEBAPP_URL),
    )]]
    await update.message.reply_text(
        "👋 *SkinVault* — трекер CS2 скинов\n\n"
        "Отслеживай стоимость портфолио, P&L и историю цен прямо в Telegram.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    print("Bot started...")
    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
