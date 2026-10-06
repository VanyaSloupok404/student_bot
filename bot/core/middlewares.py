from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.core.state import state


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool: async_sessionmaker):
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session
            return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if state.maintenance_mode:
            user_id = None
            if isinstance(event, (Message, CallbackQuery)) and event.from_user:
                user_id = event.from_user.id

            # Администраторам разрешен полный доступ даже при активной паузе
            if user_id and user_id in settings.admin_ids:
                return await handler(event, data)

            # Оповещаем пользователя о паузе
            if isinstance(event, Message):
                await event.answer("🛠 <b>Бот временно отключен на техническое обслуживание.</b>\nПожалуйста, попробуйте позже.")
            elif isinstance(event, CallbackQuery):
                await event.answer("🛠 Бот находится на техобслуживании.", show_alert=True)
            return

        return await handler(event, data)
