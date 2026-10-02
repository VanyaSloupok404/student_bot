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
        "• Отправь фото или текст с тегом <code>#предмет</code> — запишу домашнее задание.\n"
        "• Напиши быструю трату (например: <code>250 обед</code> или <code>60 проезд</code>).\n"
        "• Присылай голосовые или текст с напоминанием.\n\n"
        "Используй /help для просмотра подробных инструкций."
    )
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "📖 <b>Справка по командам и форматам:</b>\n\n"
        "1. 📅 <b>Расписание:</b>\n"
        "   Отправь изображение расписания или напиши замены.\n"
        "   Команды: /schedule — все пары, /today — на сегодня.\n\n"
        "2. 📝 <b>Домашние задания:</b>\n"
        "   Формат: <code>#математика номер 142</code> (можно прикрепить фото).\n"
        "   Команда: /hw — список активных задач.\n\n"
        "3. ⏰ <b>Напоминания:</b>\n"
        "   Напиши: «Напомни сдать отчет завтра в 10:00» или отправь голосом.\n\n"
        "4. 🌤 <b>Погода и гардероб:</b>\n"
        "   Установка города: <code>/city Москва</code>\n"
        "   Прогноз: /weather\n\n"
        "5. 💰 <b>Микро-бюджет:</b>\n"
        "   Формат: <code>200 обед</code> или <code>55 автобус</code>.\n"
        "   Команда: /expenses — сводка расходов."
    )
    await message.answer(text)

@router.message(Command("digest"))
async def cmd_test_digest(message: Message, bot: Bot) -> None:
    from bot.core.scheduler import send_evening_digest
    await send_evening_digest(bot)
