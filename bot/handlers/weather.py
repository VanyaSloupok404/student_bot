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
    await message.answer(f"✅ Город сохранен: <b>{city_name}</b>. Теперь доступны команды /wear и /weather.")


@router.message(Command("wear"))
@router.message(Command("outfit"))
async def cmd_wear(message: Message, session: AsyncSession) -> None:
    """Точная инструкция что надеть прямо сейчас в данную минуту."""
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user or not user.city:
        await message.answer("Сначала укажите ваш город командой: <code>/city Москва</code>")
        return

    data = await get_current_weather(user.city)
    if not data or "error" in data:
        err = data.get("error", "Сервис погоды временно недоступен") if data else "Ошибка соединения"
        await message.answer(f"⚠️ {err}")
        return

    b = data["breakdown"]
    text = (
        f"🧥 <b>Что надеть прямо сейчас (г. {data['city']}):</b>\n\n"
        f"🌡 <b>Температура:</b> {data['temp']:+.1f}°C (ощущается как <b>{data['feels_like']:+.1f}°C</b>)\n"
        f"☁️ <b>На улице:</b> {data['description']}, ветер {data['wind_speed']} м/с\n\n"
        f"🧥 <b>Верхняя одежда:</b> {b['outerwear']}\n"
        f"👕 <b>Базовый слой:</b> {b['base']}\n"
        f"🧣 <b>Аксессуары:</b> {b['accessories']}\n\n"
        f"💡 <i>{b['summary']}</i>"
    )
    await message.answer(text)


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
        f"🧥 <b>Рекомендация:</b> {data['advice']}\n"
        f"<i>Используйте /wear для подробной раскладки по слоям одежды.</i>"
    )
    await message.answer(text)
