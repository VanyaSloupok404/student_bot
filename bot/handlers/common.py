from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    stmt = select(User).where(User.telegram_id == message.from_user.id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        session.add(user)
        await session.commit()

    text = (
        "👋 Привет! Я твой студенческий ассистент.\n\n"
        "Чем я могу помочь:\n"
        "• Отправь фото расписания — я распознаю пары и сохраню в график.\n"
        "• Отправь фото или текст с тегом `#предмет` — запишу домашнее задание.\n"
        "• Напиши быструю трату (например: `250 обед` или `60 проезд`).\n"
        "• Присылай голосовые или текст с напоминанием.\n\n"
        "Используй /help для просмотра подробных инструкций."
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📖 Справка по командам и форматам:\n\n"
        "1. 📅 Расписание:\n"
        "   Отправь изображение расписания или напиши оперативные изменения.\n\n"
        "2. 📝 Домашние задания:\n"
        "   Формат: `#математика сделать номер 142` (можно с фото).\n\n"
        "3. ⏰ Напоминания:\n"
        "   Напиши: «Напомни сдать реферат завтра в 10:00» или отправь голосом.\n\n"
        "4. 💰 Микро-бюджет:\n"
        "   Формат: `<сумма> <категория/описание>`, например `150 кофе`."
    )
    await message.answer(text)
