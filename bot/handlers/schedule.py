import io
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Lesson, User
from bot.services.gemini import gemini_service

logger = logging.getLogger(__name__)
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

DEFAULT_BELL_TIMES = {
    1: ("08:30", "10:00"),
    2: ("10:10", "11:40"),
    3: ("12:10", "13:40"),
    4: ("13:50", "15:20"),
    5: ("15:30", "17:00"),
    6: ("17:10", "18:40"),
}

MODIFICATION_TRIGGERS = [
    "отмени", "отмена", "отменили", "не будет пары", "не будет пар",
    "не будет 1", "не будет 2", "не будет 3", "не будет 4", "не будет 5", "не будет 6",
    "замени", "замена", "вместо", "перенеси", "перенос", "перенесли",
    "добавь пару", "добавили пару"
]


@router.message(Command("clear_schedule"))
async def cmd_clear_schedule(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    await session.execute(delete(Lesson).where(Lesson.user_id == user.id))
    await session.commit()
    await message.answer("🗑 Ваше расписание полностью очищено. Теперь можно загрузить актуальное фото.")


@router.message(Command("schedule"))
async def cmd_schedule(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start для регистрации.")
        return

    stmt = select(Lesson).where(Lesson.user_id == user.id).order_by(Lesson.day_of_week, Lesson.lesson_number)
    lessons = (await session.execute(stmt)).scalars().all()

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


@router.message(Command("today"))
async def cmd_today(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    weekday = datetime.now(timezone.utc).isoweekday()
    day_name = DAYS_MAP.get(weekday, "Сегодня")

    stmt = (
        select(Lesson)
        .where(Lesson.user_id == user.id, Lesson.day_of_week == weekday)
        .order_by(Lesson.lesson_number)
    )
    lessons = (await session.execute(stmt)).scalars().all()

    if not lessons:
        await message.answer(f"🌴 На сегодня ({day_name}) пар нет.")
        return

    lines = [f"📅 <b>Пары на сегодня ({day_name}):</b>\n"]
    for l in lessons:
        room_str = f", ауд. {l.room}" if l.room else ""
        teacher_str = f" ({l.teacher})" if l.teacher else ""
        lines.append(f"{l.lesson_number}. [{l.start_time} - {l.end_time}] {l.subject}{room_str}{teacher_str}")

    await message.answer("\n".join(lines))


@router.message(F.photo, ~F.caption.startswith("#"))
async def handle_schedule_photo(message: Message, bot: Bot, session: AsyncSession) -> None:
    if not message.from_user or not message.photo:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Пожалуйста, отправьте /start перед загрузкой расписания.")
        return

    status_msg = await message.answer("⏳ Анализирую расписание на фото через Gemini...")

    try:
        photo = message.photo[-1]
        photo_file = await bot.get_file(photo.file_id)
        buffer = io.BytesIO()
        await bot.download_file(photo_file.file_path, destination=buffer)
        image_bytes = buffer.getvalue()

        now = datetime.now(timezone.utc)
        user_comment = f"Указание пользователя: '{message.caption}'" if message.caption else "Записать все дни"
        context = f"Current UTC datetime: {now.strftime('%Y-%m-%d %H:%M:%S')}, weekday: {now.isoweekday()}. {user_comment}"

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

        lessons_to_add: list[Lesson] = []
        processed_days = []

        for day in days:
            day_of_week = day.get("day_of_week")
            if not day_of_week or not isinstance(day_of_week, int):
                continue
            parity = day.get("parity") or "all"
            processed_days.append(DAYS_MAP.get(day_of_week, f"День {day_of_week}"))

            await session.execute(
                delete(Lesson).where(
                    Lesson.user_id == user.id,
                    Lesson.day_of_week == day_of_week,
                    Lesson.parity == parity,
                )
            )

            for les in day.get("lessons", []):
                les_num = les.get("lesson_number") or 1
                default_start, default_end = DEFAULT_BELL_TIMES.get(les_num, ("08:30", "10:00"))

                start_time = les.get("start_time") or default_start
                end_time = les.get("end_time") or default_end

                lesson_obj = Lesson(
                    user_id=user.id,
                    day_of_week=day_of_week,
                    parity=parity,
                    lesson_number=les_num,
                    subject=les.get("subject") or "Предмет",
                    start_time=start_time,
                    end_time=end_time,
                    room=les.get("room"),
                    teacher=les.get("teacher"),
                    subgroup=les.get("subgroup") or 0,
                )
                lessons_to_add.append(lesson_obj)

        session.add_all(lessons_to_add)
        await session.commit()

        count = len(lessons_to_add)
        days_str = ", ".join(dict.fromkeys(processed_days))
        await status_msg.edit_text(
            f"✅ Расписание сохранено для: <b>{days_str}</b> (всего {count} пар)!\n"
            f"Используйте /schedule для просмотра всего графика или /today для текущего дня."
        )

    except Exception as exc:
        logger.error("Schedule processing failed: %s", exc, exc_info=True)
        await session.rollback()
        await status_msg.edit_text(f"❌ Произошла ошибка при сохранении расписания: {exc}")


@router.message(F.text, ~F.text.startswith("/"), ~F.text.startswith("#"))
async def handle_schedule_modifications(message: Message, session: AsyncSession) -> None:
    """Точечная текстовая отмена, замена предмета или перенос кабинета."""
    if not message.from_user or not message.text:
        return

    text_lower = message.text.lower()
    if not any(trigger in text_lower for trigger in MODIFICATION_TRIGGERS):
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    status_msg = await message.answer("⏳ Анализирую изменение расписания...")

    tz_str = user.timezone or "Europe/Moscow"
    user_tz = ZoneInfo(tz_str)
    now_local = datetime.now(user_tz)
    weekday = now_local.isoweekday()

    context = (
        f"User local timezone: {tz_str}, "
        f"Current local datetime: {now_local.strftime('%Y-%m-%d %H:%M:%S')}, "
        f"Today weekday: {weekday} ({DAYS_MAP.get(weekday, 'День')})"
    )

    res = await gemini_service.parse_schedule_modification(message.text, context=context)
    if res.get("status") == "error":
        err = res.get("error_message", "Не удалось распознать изменения")
        await status_msg.edit_text(f"❌ Ошибка: {err}")
        return

    data = res.get("data", {})
    operation = data.get("operation")
    day_of_week = data.get("day_of_week") or weekday
    lesson_number = data.get("lesson_number")
    target_subject = data.get("target_subject")
    new_subject = data.get("new_subject")
    new_room = data.get("new_room")
    explanation = data.get("explanation", "Расписание изменено")

    day_name = DAYS_MAP.get(day_of_week, f"День {day_of_week}")

    try:
        if operation == "cancel_lesson":
            # Удаление / отмена пары
            if lesson_number:
                await session.execute(
                    delete(Lesson).where(
                        Lesson.user_id == user.id,
                        Lesson.day_of_week == day_of_week,
                        Lesson.lesson_number == lesson_number,
                    )
                )
            elif target_subject:
                await session.execute(
                    delete(Lesson).where(
                        Lesson.user_id == user.id,
                        Lesson.day_of_week == day_of_week,
                        Lesson.subject.ilike(f"%{target_subject}%"),
                    )
                )
            await session.commit()

        elif operation in ("replace_lesson", "move_room", "add_lesson"):
            # Поиск существующей пары
            query = select(Lesson).where(Lesson.user_id == user.id, Lesson.day_of_week == day_of_week)
            if lesson_number:
                query = query.where(Lesson.lesson_number == lesson_number)
            elif target_subject:
                query = query.where(Lesson.subject.ilike(f"%{target_subject}%"))

            existing_lesson = (await session.execute(query)).scalar_one_or_none()

            if existing_lesson:
                if new_subject:
                    existing_lesson.subject = new_subject
                if new_room:
                    existing_lesson.room = new_room
            else:
                les_num = lesson_number or 1
                default_start, default_end = DEFAULT_BELL_TIMES.get(les_num, ("08:30", "10:00"))
                new_les = Lesson(
                    user_id=user.id,
                    day_of_week=day_of_week,
                    lesson_number=les_num,
                    subject=new_subject or target_subject or "Пара",
                    start_time=default_start,
                    end_time=default_end,
                    room=new_room,
                )
                session.add(new_les)

            await session.commit()

        # Формируем актуальное расписание на этот день
        updated_lessons = (
            await session.execute(
                select(Lesson)
                .where(Lesson.user_id == user.id, Lesson.day_of_week == day_of_week)
                .order_by(Lesson.lesson_number)
            )
        ).scalars().all()

        if updated_lessons:
            lines = [f"✅ <b>{explanation}</b>\n\n📅 <b>Расписание на {day_name}:</b>"]
            for l in updated_lessons:
                r_str = f", ауд. {l.room}" if l.room else ""
                lines.append(f"{l.lesson_number}. [{l.start_time} - {l.end_time}] {l.subject}{r_str}")
            reply_text = "\n".join(lines)
        else:
            reply_text = f"✅ <b>{explanation}</b>\n\n🌴 На {day_name} теперь пар нет."

        await status_msg.edit_text(reply_text)

    except Exception as exc:
        logger.error("Schedule modification error: %s", exc)
        await session.rollback()
        await status_msg.edit_text(f"❌ Ошибка при изменении расписания: {exc}")
