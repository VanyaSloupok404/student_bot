import asyncio
import os
import resource
import sys
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.core.state import state
from bot.database.models import Expense, Homework, Lesson, Reminder, TodoItem, User

router = Router(name="admin")


def get_admin_keyboard() -> InlineKeyboardMarkup:
    maint_btn_text = "▶️ Снять с паузы" if state.maintenance_mode else "⏸ Включить паузу"
    keyboard = [
        [InlineKeyboardButton(text="📊 Статистика системы и БД", callback_data="admin_stats")],
        [InlineKeyboardButton(text=maint_btn_text, callback_data="admin_toggle_maint")],
        [InlineKeyboardButton(text="🔄 Перезапустить процесс", callback_data="admin_restart")],
        [InlineKeyboardButton(text="📢 Рассылка пользователям", callback_data="admin_broadcast_info")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_admin_text() -> str:
    maint_status = "🔴 ВКЛЮЧЕНА (пользователи заблокированы)" if state.maintenance_mode else "🟢 ВЫКЛЮЧЕНА (бот активен)"
    return (
        f"👑 <b>Панель администратора</b>\n\n"
        f"• PID процесса: <code>{os.getpid()}</code>\n"
        f"• Пауза (техобслуживание): {maint_status}\n"
        f"• Администраторы: <code>{settings.admin_ids}</code>\n\n"
        f"Выберите действие ниже:"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not message.from_user or message.from_user.id not in settings.admin_ids:
        return

    await message.answer(get_admin_text(), reply_markup=get_admin_keyboard())


@router.callback_query(F.data == "admin_menu")
async def cb_admin_menu(call: CallbackQuery) -> None:
    if not call.from_user or call.from_user.id not in settings.admin_ids or not call.message:
        return

    await call.message.edit_text(get_admin_text(), reply_markup=get_admin_keyboard())
    await call.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.from_user or call.from_user.id not in settings.admin_ids or not call.message:
        return

    users_cnt = (await session.execute(select(func.count(User.id)))).scalar() or 0
    lessons_cnt = (await session.execute(select(func.count(Lesson.id)))).scalar() or 0
    hw_cnt = (await session.execute(select(func.count(Homework.id)).where(Homework.is_completed == False))).scalar() or 0  # noqa: E712
    rem_cnt = (await session.execute(select(func.count(Reminder.id)))).scalar() or 0
    todos_cnt = (await session.execute(select(func.count(TodoItem.id)))).scalar() or 0

    exp_res = (await session.execute(select(func.count(Expense.id), func.sum(Expense.amount)))).one()
    exp_cnt = exp_res[0] or 0
    exp_sum = exp_res[1] or 0.0

    # Потребление памяти процессом в МБ (Linux resident set size)
    ram_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024

    stats_text = (
        f"📊 <b>Системная статистика бота:</b>\n\n"
        f"👥 Пользователей в базе: <b>{users_cnt}</b>\n"
        f"📚 Всего пар в графике: <b>{lessons_cnt}</b>\n"
        f"📝 Активных заданий (ДЗ): <b>{hw_cnt}</b>\n"
        f"⏰ Запланированных напоминаний: <b>{rem_cnt}</b>\n"
        f"📋 Задач в чеклистах (Todo): <b>{todos_cnt}</b>\n"
        f"💰 Записано трат: <b>{exp_cnt}</b> (на сумму <b>{exp_sum:.2f} руб.</b>)\n\n"
        f"⚙️ <b>Ресурсы сервера:</b>\n"
        f"• RAM процесса: <b>~{ram_mb} МБ</b>\n"
        f"• PID процесса: <code>{os.getpid()}</code>\n"
    )

    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="admin_menu")]])
    await call.message.edit_text(stats_text, reply_markup=back_kb)
    await call.answer()


@router.callback_query(F.data == "admin_toggle_maint")
async def cb_admin_toggle_maint(call: CallbackQuery) -> None:
    if not call.from_user or call.from_user.id not in settings.admin_ids or not call.message:
        return

    state.maintenance_mode = not state.maintenance_mode
    status_str = "включен (обычные пользователи заблокированы)" if state.maintenance_mode else "отключен (бот доступен всем)"
    await call.answer(f"Режим паузы {status_str}", show_alert=True)
    await call.message.edit_text(get_admin_text(), reply_markup=get_admin_keyboard())


@router.callback_query(F.data == "admin_restart")
async def cb_admin_restart(call: CallbackQuery, bot: Bot) -> None:
    if not call.from_user or call.from_user.id not in settings.admin_ids or not call.message:
        return

    await call.answer("Перезапуск процесса...", show_alert=True)
    await call.message.edit_text("🔄 <b>Перезапуск процесса бота...</b>\nНовый инстанс запустится через 1-2 секунды.")

    # Закрываем сессию Telegram и перезапускаем процесс ОС
    await bot.session.close()
    os.execv(sys.executable, [sys.executable, "-m", "bot.main"])


@router.callback_query(F.data == "admin_broadcast_info")
async def cb_admin_broadcast_info(call: CallbackQuery) -> None:
    if not call.from_user or call.from_user.id not in settings.admin_ids or not call.message:
        return

    text = (
        "📢 <b>Массовая рассылка:</b>\n\n"
        "Для отправки объявления всем пользователям бота отправьте команду:\n"
        "<code>/broadcast Ваше важное сообщение или объявление</code>"
    )
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="admin_menu")]])
    await call.message.edit_text(text, reply_markup=back_kb)
    await call.answer()


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, bot: Bot, session: AsyncSession) -> None:
    if not message.from_user or message.from_user.id not in settings.admin_ids:
        return

    if not command.args or not command.args.strip():
        await message.answer("⚠️ Укажите текст для рассылки, например:\n<code>/broadcast Завтра колледж закрыт на санобработку!</code>")
        return

    broadcast_text = f"📢 <b>Объявление:</b>\n\n{command.args.strip()}"
    users = (await session.execute(select(User.telegram_id))).scalars().all()

    sent = 0
    for tg_id in users:
        try:
            await bot.send_message(chat_id=tg_id, text=broadcast_text)
            sent += 1
            await asyncio.sleep(0.05)  # Защита от лимитов Telegram (20 сообщ/сек)
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена. Успешно отправлено: <b>{sent} / {len(users)}</b> пользователям.")
