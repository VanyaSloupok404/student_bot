from aiogram import Bot, Router
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
        "• Команда /wear подскажет, что надеть прямо в эту минуту.\n"
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
        "   Отправь фото расписания (можно с подписью, например: «только понедельник»).\n"
        "   Команды: /schedule — все пары, /today — на сегодня, /clear_schedule — сброс.\n\n"
        "2. 📝 <b>Домашние задания:</b>\n"
        "   Формат: <code>#математика номер 142</code> (можно с фото задания).\n"
        "   Команда: /hw — список активных задач с кнопкой [Выполнено].\n\n"
        "3. 🧥 <b>Погода и гардероб:</b>\n"
        "   /wear — <b>что надеть прямо сейчас</b> (верхняя одежда, слои, зонт).\n"
        "   /city — установить город (например, <code>/city Москва</code>).\n"
        "   /weather — общая метеорологическая сводка.\n\n"
        "4. 🗺 <b>Маршрут до колледжа:</b>\n"
        "   /commute — расчет времени в пути и время выхода к 1-й паре.\n\n"
        "5. ⏰ <b>Напоминания и часовой пояс:</b>\n"
        "   Напиши: «Напомни завтра в 10:00 сдать отчет» или отправь голосом.\n"
        "   Команда: /tz — проверить или изменить часовой пояс.\n\n"
        "6. 💰 <b>Микро-бюджет:</b>\n"
        "   Формат: <code>200 обед</code> или <code>55 автобус</code>.\n"
        "   Команда: /expenses — сводка расходов."
    )
    await message.answer(text)


@router.message(Command("digest"))
async def cmd_test_digest(message: Message, bot: Bot) -> None:
    from bot.core.scheduler import send_evening_digest
    await send_evening_digest(bot)
