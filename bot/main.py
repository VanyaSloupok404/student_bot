import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.core.database import async_session_maker, engine, init_db
from bot.core.middlewares import DbSessionMiddleware
from bot.core.scheduler import setup_scheduler
from bot.handlers import common, commute, homework, quick_actions, schedule, weather

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

    # Middleware сессий БД
    db_middleware = DbSessionMiddleware(session_pool=async_session_maker)
    dp.message.middleware(db_middleware)
    dp.callback_query.middleware(db_middleware)

    # Регистрация функциональных роутеров
    dp.include_router(common.router)
    dp.include_router(schedule.router)
    dp.include_router(homework.router)
    dp.include_router(weather.router)
    dp.include_router(commute.router)
    dp.include_router(quick_actions.router)

    # Запуск планировщика задач
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("APScheduler started.")

    logger.info("Starting bot polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        logger.info("Closing bot session and DB engine...")
        await bot.session.close()
        await engine.dispose()
        logger.info("Application stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution terminated by user.")
