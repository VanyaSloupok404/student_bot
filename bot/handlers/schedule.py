import io
from datetime import datetime, timezone
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Lesson, User
from bot.services.gemini import gemini_service

router = Router(name="schedule")

DAYS_MAP = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье",
}


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_stmt = select(User).where(User.telegram_id == message.from_user.id)
    user_res = await session.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start для регистрации.")
        return

    stmt = select(Lesson).where(Lesson.user_id == user.id).order_by(Lesson.day_of_week, Lesson.lesson_number)
    result = await session.execute(stmt)
    lessons = result.scalars().all()

    if not lessons:
        await message.answer("📅 Расписание пока не заполнено. Отправьте фото расписания, чтобы я его добавил.")
        return

    lines = ["📅 <b>Ваше расписание:</b>\n"]
    current_day = None
    for lesson in lessons:
        if lesson.day_of_week != current_day:
            current_day = lesson.day_of_week
            day_name = DAYS_MAP.get(current_day, f"День {current_day}")
            lines.append(f"\n<b>{day_name}:</b>")
        room_str = f", ауд. {lesson.room}" if lesson.room else ""
        teacher_str = f" ({lesson.teacher})" if lesson.teacher else ""
        lines.append(
            f"{lesson.lesson_number}. [{lesson.start_time} - {lesson.end_time}] {lesson.subject}{room_str}{teacher_str}"
        )

    await message.answer("\n".join(lines))


@router.message(F.photo, ~F.caption.startswith("#"))
async def handle_schedule_photo(message: Message, bot: Bot, session: AsyncSession) -> None:
    if not message.from_user or not message.photo:
        return

    user_stmt = select(User).where(User.telegram_id == message.from_user.id)
    user_res = await session.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Пожалуйста, отправьте /start перед загрузкой расписания.")
        return

    status_msg = await message.answer("⏳ Анализирую расписание на фото через Gemini...")

    photo = message.photo[-1]
    photo_file = await bot.get_file(photo.file_id)
    buffer = io.BytesIO()
    await bot.download_file(photo_file.file_path, destination=buffer)
    image_bytes = buffer.getvalue()

    now = datetime.now(timezone.utc)
    context = f"Current UTC datetime: {now.strftime('%Y-%m-%d %H:%M:%S')}, weekday: {now.isoweekday()}"

    res = await gemini_service.parse_schedule(image_bytes=image_bytes, mime_type="image/jpeg", context=context)

    if res.get("status") == "error":
        err = res.get("error_message", "Не удалось распознать расписание")
        await status_msg.edit_text(f"❌ Ошибка распознавания: {err}")
        return

    data = res.get("data", {})
    days = data.get("days", [])
    if not days:
        await status_msg.edit_text("⚠️ В ответе нейросети не найдено занятий. Попробуйте сделать фото четче.")
        return

    for day in days:
        day_of_week = day.get("day_of_week")
        if not day_of_week:
            continue
        parity = day.get("parity", "all")

        await session.execute(
            delete(Lesson).where(
                Lesson.user_id == user.id,
                Lesson.day_of_week == day_of_week,
                Lesson.parity == parity,
            )
        )

        for les in day.get("lessons", []):
            lesson_obj = Lesson(
                user_id=user.id,
                day_of_week=day_of_week,
                parity=parity,
                lesson_number=les.get("lesson_number", 1),
                subject=les.get("subject", "Без названия"),
                start_time=les.get("start_time", "00:00"),
                end_time=les.get("end_time", "00:00"),
                room=les.get("room"),
                teacher=les.get("teacher"),
                subgroup=les.get("subgroup", 0),
            )
            session.add(lesson_obj)

    await session.commit()
    await status_msg.edit_text("✅ Расписание успешно сохранено в базу данных! Нажмите /schedule для просмотра.")
