from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.services.weather import get_current_weather

router = Router(name="weather")


@router.message(Command("city"))
async def cmd_set_city(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    city_name = command.args.strip() if command.args else None
    if not city_name:
        current = user.city or "не указан"
        await message.answer(f"Текущий город: <b>{current}</b>\nДля изменения введите: <code>/city Москва</code>")
        return

    user.city = city_name
    await session.commit()
    await message.answer(f"✅ Город успешно сохранен: <b>{city_name}</b>. Теперь можно запросить погоду командой /weather.")


@router.message(Command("weather"))
async def cmd_weather(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user or not user.city:
        await message.answer("Укажите ваш город с помощью команды: <code>/city Москва</code>")
        return

    data = await get_current_weather(user.city)
    if not data or "error" in data:
        err = data.get("error", "Не удалось получить данные о погоде") if data else "Сервис недоступен"
        await message.answer(f"⚠️ {err}")
        return

    text = (
        f"🌤 <b>Погода в г. {data['city']}:</b>\n"
        f"• Температура: <b>{data['temp']}°C</b> (ощущается как {data['feels_like']}°C)\n"
        f"• Состояние: {data['description']}\n"
        f"• Ветер: {data['wind_speed']} м/с\n\n"
        f"🧥 <b>Рекомендация по одежде:</b>\n{data['advice']}"
    )
    await message.answer(text)
