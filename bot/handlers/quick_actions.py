import io
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from google.genai import types
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Expense, Reminder, User
from bot.services.gemini import gemini_service

router = Router(name="quick_actions")

EXPENSE_PATTERN = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:руб(?:лей|\.?)?)?\s*(.+)$", re.IGNORECASE)


@router.message(Command("tz"))
async def cmd_timezone(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    tz_arg = command.args.strip() if command.args else None
    if not tz_arg:
        current_tz = user.timezone or "Europe/Moscow"
        now_local = datetime.now(ZoneInfo(current_tz)).strftime("%H:%M:%S")
        await message.answer(
            f"🌍 Ваш часовой пояс: <b>{current_tz}</b> (время на часах: <b>{now_local}</b>)\n"
            f"Для изменения укажите зону, например: <code>/tz Europe/Moscow</code> или <code>/tz Asia/Yekaterinburg</code>"
        )
        return

    try:
        ZoneInfo(tz_arg)
        user.timezone = tz_arg
        await session.commit()
        await message.answer(f"✅ Часовой пояс обновлен: <b>{tz_arg}</b>")
    except Exception:
        await message.answer("⚠️ Неверный часовой пояс. Примеры: <code>Europe/Moscow</code>, <code>Asia/Yekaterinburg</code>, <code>Asia/Novosibirsk</code>.")


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

    user_tz_str = user.timezone or "Europe/Moscow"
    user_tz = ZoneInfo(user_tz_str)
    now_local = datetime.now(user_tz)
    context = (
        f"User local timezone: {user_tz_str}, "
        f"Current local datetime: {now_local.strftime('%Y-%m-%d %H:%M:%S')}, "
        f"weekday: {now_local.isoweekday()}"
    )

    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg")
    prompt = (
        f"Расшифруй аудиосообщение и определи действие (reminder или expense). "
        f"Возвращай target_datetime строго в локальном времени пользователя. Контекст: {context}"
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
        if dt_str:
            local_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=user_tz)
            target_utc = local_dt.astimezone(timezone.utc)
        else:
            local_dt = now_local
            target_utc = datetime.now(timezone.utc)

        reminder = Reminder(user_id=user.id, text=text, trigger_datetime=target_utc)
        session.add(reminder)
        await session.commit()
        await status_msg.edit_text(f"⏰ <b>Напоминание создано:</b> «{text}» на {local_dt.strftime('%d.%m в %H:%M')}")
    else:
        await status_msg.edit_text("ℹ️ Аудио расшифровано, но конкретное действие (напоминание/трата) не распознано.")


@router.message(F.text)
async def handle_text_shortcuts(message: Message, session: AsyncSession) -> None:
    if not message.from_user or not message.text or message.text.startswith("/"):
        return

    # Быстрый ввод расхода: "238 рублей ролл+онигири" или "250 обед"
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

    # Запросы на естественном языке
    lowered = message.text.lower()
    if lowered.startswith("напомни") or "заметка" in lowered:
        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar_one_or_none()
        if not user:
            await message.answer("Сначала отправьте /start.")
            return

        user_tz_str = user.timezone or "Europe/Moscow"
        user_tz = ZoneInfo(user_tz_str)
        now_local = datetime.now(user_tz)
        context = (
            f"User local timezone: {user_tz_str}, "
            f"Current local datetime: {now_local.strftime('%Y-%m-%d %H:%M:%S')}, "
            f"weekday: {now_local.isoweekday()}. "
            f"ВАЖНО: верни target_datetime строго в локальном времени пользователя."
        )

        res = await gemini_service.parse_quick_action(text=message.text, context=context)

        if res.get("status") == "success" and res.get("data", {}).get("type") == "reminder":
            payload = res["data"]["payload"]
            text = payload.get("text", message.text)
            dt_str = payload.get("target_datetime")
            if dt_str:
                local_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=user_tz)
                target_utc = local_dt.astimezone(timezone.utc)
            else:
                local_dt = now_local
                target_utc = datetime.now(timezone.utc)

            reminder = Reminder(user_id=user.id, text=text, trigger_datetime=target_utc)
            session.add(reminder)
            await session.commit()
            await message.answer(f"⏰ Напоминание сохранено: «{text}» на <b>{local_dt.strftime('%d.%m в %H:%M')}</b>")
            return
