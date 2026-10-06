import re
from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TodoItem, User

router = Router(name="todo")


def render_todo_view(items: list[TodoItem]) -> tuple[str, InlineKeyboardMarkup]:
    if not items:
        return "📋 <b>Список задач пуст!</b>\nДобавьте новые пункты: <code>/todo тетрадь, ручка, кофе</code>", InlineKeyboardMarkup(inline_keyboard=[])

    lines = ["📋 <b>Ваш список задач и покупок:</b>\n"]
    buttons = []

    for idx, item in enumerate(items, start=1):
        if item.is_done:
            lines.append(f"{idx}. ✅ <s>{item.text}</s>")
            btn_text = f"✅ {item.text[:25]}"
        else:
            lines.append(f"{idx}. ⬜️ {item.text}")
            btn_text = f"⬜️ {item.text[:25]}"

        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"todo_toggle:{item.id}")])

    bottom_row = []
    if any(item.is_done for item in items):
        bottom_row.append(InlineKeyboardButton(text="🗑 Удалить выполненные", callback_data="todo_clear_done"))
    bottom_row.append(InlineKeyboardButton(text="❌ Очистить весь список", callback_data="todo_clear_all"))

    buttons.append(bottom_row)
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return "\n".join(lines), keyboard


@router.message(Command("todo"))
@router.message(Command("buy"))
async def cmd_todo(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    args = command.args.strip() if command.args else None

    # Добавление новых пунктов через запятую или перевод строки
    if args:
        raw_items = [p.strip() for p in re.split(r"[,;\n]+", args) if p.strip()]
        for text in raw_items:
            session.add(TodoItem(user_id=user.id, text=text, is_done=False))
        await session.commit()

    # Загружаем актуальный список
    stmt = select(TodoItem).where(TodoItem.user_id == user.id).order_by(TodoItem.is_done, TodoItem.created_at)
    items = (await session.execute(stmt)).scalars().all()

    msg_text, markup = render_todo_view(items)
    await message.answer(msg_text, reply_markup=markup)


@router.callback_query(F.data.startswith("todo_toggle:"))
async def cb_todo_toggle(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.data or not call.message or not call.from_user:
        return

    item_id = int(call.data.split(":")[1])
    item = await session.get(TodoItem, item_id)
    if not item:
        await call.answer("Пункт не найден или уже удален.", show_alert=True)
        return

    item.is_done = not item.is_done
    await session.commit()
    await call.answer()

    user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        return

    stmt = select(TodoItem).where(TodoItem.user_id == user.id).order_by(TodoItem.is_done, TodoItem.created_at)
    items = (await session.execute(stmt)).scalars().all()
    msg_text, markup = render_todo_view(items)

    try:
        await call.message.edit_text(msg_text, reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data == "todo_clear_done")
async def cb_todo_clear_done(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.message or not call.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        return

    await session.execute(delete(TodoItem).where(TodoItem.user_id == user.id, TodoItem.is_done == True))  # noqa: E712
    await session.commit()
    await call.answer("Выполненные пункты удалены")

    stmt = select(TodoItem).where(TodoItem.user_id == user.id).order_by(TodoItem.is_done, TodoItem.created_at)
    items = (await session.execute(stmt)).scalars().all()
    msg_text, markup = render_todo_view(items)

    try:
        await call.message.edit_text(msg_text, reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data == "todo_clear_all")
async def cb_todo_clear_all(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.message or not call.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == call.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        return

    await session.execute(delete(TodoItem).where(TodoItem.user_id == user.id))
    await session.commit()
    await call.answer("Список полностью очищен")
    await call.message.edit_text("🗑 <b>Список задач полностью очищен.</b>\nДобавить новые: <code>/todo пункт 1, пункт 2</code>", reply_markup=None)
