"""
ProgrEVER — Telegram Account Auto-Warmer Bot
Entry point
"""

import asyncio
import logging
import os

from telegram import BotCommand
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

from config import BOT_TOKEN, ADMIN_IDS, SESSIONS_DIR
from database import init_db
from warming_engine import init_scheduler, stop_scheduler, ensure_schedules
from bot.handlers import (
    cmd_start, cmd_status, callback_handler, menu_text_handler,
    build_add_account_conv,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application):
    await init_db()
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs("media", exist_ok=True)

    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
    ])

    init_scheduler(app)
    await ensure_schedules()
    logger.info("ProgrEVER bot started!")


async def post_shutdown(app: Application):
    stop_scheduler()
    logger.info("ProgrEVER bot stopped.")


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set! Check your .env file.")
        return

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(build_add_account_conv())
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text_handler))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Starting polling...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
