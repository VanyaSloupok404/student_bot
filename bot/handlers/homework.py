import io
from datetime import date, datetime, timedelta, timezone
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Homework, Lesson, User
from bot.services.gemini import gemini_service

router = Router(name="homework")


async def get_next_lesson_date(session: AsyncSession, user_id: int, subject: str) -> date:
    stmt = select(Lesson.day_of_week).where(
        Lesson.user_id == user_id,
        Lesson.subject.ilike(f"%{subject}%"),
    )
    result = await session.execute(stmt)
    days = result.scalars().all()
    today = datetime.now(timezone.utc).date()
    current_weekday = today.isoweekday()

    if not days:
        return today + timedelta(days=1)

    future_offsets = [(d - current_weekday) % 7 for d in days]
    future_offsets = [offset if offset > 0 else 7 for offset in future_offsets]
    min_offset = min(future_offsets)
    return today + timedelta(days=min_offset)


@router.message(Command("hw"))
@router.message(Command("homework"))
async def cmd_homework(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    stmt = (
        select(Homework)
        .where(Homework.user_id == user.id, Homework.is_completed == False)  # noqa: E712
        .order_by(Homework.deadline_date)
    )
    res = await session.execute(stmt)
    tasks = res.scalars().all()

    if not tasks:
        await message.answer("🎉 Все домашние задания выполнены! Нет активных задач.")
        return

    for task in tasks:
        dl_str = task.deadline_date.strftime("%d.%m.%Y") if task.deadline_date else "Не указан"
        text = f"📚 <b>{task.subject}</b>\n📝 {task.task_text}\n⏰ Дедлайн: <b>{dl_str}</b>"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"hw_done:{task.id}")],
            ]
        )
        if task.photo_file_id:
            await message.answer_photo(photo=task.photo_file_id, caption=text, reply_markup=keyboard)
        else:
            await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("hw_done:"))
async def callback_hw_done(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.data or not call.message:
        return

    task_id = int(call.data.split(":")[1])
    task = await session.get(Homework, task_id)
    if not task:
        await call.answer("Задание не найдено", show_alert=True)
        return

    task.is_completed = True
    await session.commit()
    await call.answer("Отлично! Задание помечено выполненным.")
    await call.message.edit_reply_markup(reply_markup=None)
    if isinstance(call.message, Message):
        if call.message.caption:
            await call.message.edit_caption(caption=f"{call.message.caption}\n\n✅ <b>Выполнено</b>")
        elif call.message.text:
            await call.message.edit_text(text=f"{call.message.text}\n\n✅ <b>Выполнено</b>")


@router.message(F.text.startswith("#"))
@router.message(F.photo, F.caption.startswith("#"))
async def handle_homework_input(message: Message, bot: Bot, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    raw_text = message.caption if message.photo else message.text
    photo_file_id = None
    image_bytes = None

    if message.photo:
        photo = message.photo[-1]
        photo_file_id = photo.file_id
        photo_file = await bot.get_file(photo.file_id)
        buffer = io.BytesIO()
        await bot.download_file(photo_file.file_path, destination=buffer)
        image_bytes = buffer.getvalue()

    status_msg = await message.answer("⏳ Анализирую домашнее задание...")

    now = datetime.now(timezone.utc)
    context = f"Current UTC date: {now.strftime('%Y-%m-%d')}"
    res = await gemini_service.parse_homework(
        text=raw_text or "",
        image_bytes=image_bytes,
        mime_type="image/jpeg",
        context=context,
    )

    if res.get("status") == "error":
        err = res.get("error_message", "Не удалось извлечь задание")
        await status_msg.edit_text(f"❌ Ошибка: {err}")
        return

    data = res.get("data", {})
    subject = data.get("subject", "Предмет")
    task_text = data.get("task_text", raw_text)
    deadline_date_str = data.get("deadline_date")

    deadline = None
    if deadline_date_str:
        try:
            deadline = datetime.strptime(deadline_date_str, "%Y-%m-%d").date()
        except ValueError:
            deadline = None

    if not deadline:
        deadline = await get_next_lesson_date(session=session, user_id=user.id, subject=subject)

    hw = Homework(
        user_id=user.id,
        subject=subject,
        task_text=task_text,
        photo_file_id=photo_file_id,
        deadline_date=deadline,
        is_completed=False,
    )
    session.add(hw)
    await session.commit()

    dl_str = deadline.strftime("%d.%m.%Y")
    await status_msg.edit_text(
        f"✅ <b>Домашнее задание сохранено!</b>\n"
        f"📚 Предмет: {subject}\n"
        f"📝 Задание: {task_text}\n"
        f"⏰ Авто-дедлайн: {dl_str}\n\n"
        f"Для просмотра списка активных задач используйте /hw"
    )
