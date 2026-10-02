import io
import re
from datetime import datetime, timezone
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from google.genai import types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Expense, Reminder, User
from bot.services.gemini import gemini_service

router = Router(name="quick_actions")

EXPENSE_PATTERN = re.compile(r"^(\d+(?:[.,]\d+)?)\s+(.+)$")


@router.message(Command("expenses"))
async def cmd_expenses(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    stmt = select(func.sum(Expense.amount), func.count(Expense.id)).where(Expense.user_id == user.id)
    res = await session.execute(stmt)
    total, count = res.one()

    if not count:
        await message.answer("💸 У вас пока нет записанных трат.\nФормат добавления: <code>150 кофе</code> или <code>50 проезд</code>")
        return

    await message.answer(f"📊 <b>Ваши расходы:</b>\nВсего записей: {count}\nОбщая сумма: <b>{total:.2f} руб.</b>")


@router.message(F.voice)
async def handle_voice_message(message: Message, bot: Bot, session: AsyncSession) -> None:
    if not message.from_user or not message.voice:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    status_msg = await message.answer("🎙 Слушаю и анализирую аудиосообщение...")

    voice_file = await bot.get_file(message.voice.file_id)
    buffer = io.BytesIO()
    await bot.download_file(voice_file.file_path, destination=buffer)
    audio_bytes = buffer.getvalue()

    now = datetime.now(timezone.utc)
    context = f"Current UTC datetime: {now.strftime('%Y-%m-%d %H:%M:%S')}"

    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
    prompt = (
        f"Расшифруй аудиосообщение и извлеки действие согласно инструкциям (reminder или expense). "
        f"Контекст: {context}"
    )

    res = await gemini_service._generate([audio_part, prompt])
    if res.get("status") == "error":
        err = res.get("error_message", "Не удалось распознать голос")
        await status_msg.edit_text(f"❌ {err}")
        return

    data = res.get("data", {})
    action_type = data.get("type")
    payload = data.get("payload", {})

    if action_type == "expense":
        amount = float(payload.get("amount", 0.0))
        category = payload.get("category", "other")
        desc = payload.get("text", "Голосовая запись")
        expense = Expense(user_id=user.id, amount=amount, category=category, description=desc)
        session.add(expense)
        await session.commit()
        await status_msg.edit_text(f"💰 <b>Расход записан:</b> {amount:.2f} руб. ({desc})")

    elif action_type == "reminder":
        text = payload.get("text", "Напоминание")
        dt_str = payload.get("target_datetime")
        target_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if dt_str else now
        reminder = Reminder(user_id=user.id, text=text, trigger_datetime=target_dt)
        session.add(reminder)
        await session.commit()
        await status_msg.edit_text(f"⏰ <b>Напоминание создано:</b> «{text}» на {target_dt.strftime('%d.%m %H:%M UTC')}")
    else:
        await status_msg.edit_text("ℹ️ Аудио расшифровано, но конкретное действие (напоминание/трата) не распознано.")


@router.message(F.text)
async def handle_text_shortcuts(message: Message, session: AsyncSession) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return

    # Быстрый ввод расхода: "250 обед"
    match = EXPENSE_PATTERN.match(message.text.strip())
    if match:
        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar_one_or_none()
        if not user:
            await message.answer("Сначала отправьте /start.")
            return

        amount = float(match.group(1).replace(",", "."))
        desc = match.group(2).strip()

        expense = Expense(user_id=user.id, amount=amount, category="general", description=desc)
        session.add(expense)
        await session.commit()
        await message.answer(f"💰 Записан расход: <b>{amount:.2f} руб.</b> ({desc})")
        return

    # Запросы на естественном языке с ключевыми словами
    lowered = message.text.lower()
    if lowered.startswith("напомни") or "заметка" in lowered:
        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar_one_or_none()
        if not user:
            await message.answer("Сначала отправьте /start.")
            return

        now = datetime.now(timezone.utc)
        context = f"Current UTC datetime: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        res = await gemini_service.parse_quick_action(text=message.text, context=context)

        if res.get("status") == "success" and res.get("data", {}).get("type") == "reminder":
            payload = res["data"]["payload"]
            text = payload.get("text", message.text)
            dt_str = payload.get("target_datetime")
            target_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if dt_str else now
            reminder = Reminder(user_id=user.id, text=text, trigger_datetime=target_dt)
            session.add(reminder)
            await session.commit()
            await message.answer(f"⏰ Напоминание сохранено: «{text}» на {target_dt.strftime('%d.%m %H:%M UTC')}")
            return
