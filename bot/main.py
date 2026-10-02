import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.core.database import async_session_maker, engine, init_db
from bot.core.middlewares import DbSessionMiddleware
from bot.handlers import common

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Initializing database...")
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Регистрация middleware для сессий БД
    db_middleware = DbSessionMiddleware(session_pool=async_session_maker)
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)

    # Регистрация роутеров
    dp.include_router(common.router)

    logger.info("Starting bot polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Closing storage and bot session...")
        await bot.session.close()
        await engine.dispose()
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated by user.")
