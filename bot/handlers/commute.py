from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.services.commute import calculate_commute

router = Router(name="commute")


@router.message(Command("commute"))
@router.message(Command("route"))
async def cmd_commute(message: Message, command: CommandObject, session: AsyncSession) -> None:
    if not message.from_user:
        return

    user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = user_res.scalar_one_or_none()
    if not user:
        await message.answer("Сначала отправьте /start.")
        return

    args = command.args.strip() if command.args else None

    # Сценарий 1: Просмотр текущих настроек маршрута
    if not args:
        if not user.commute_minutes:
            await message.answer(
                "🗺 <b>Маршрут до колледжа пока не настроен.</b>\n\n"
                "<b>Варианты настройки:</b>\n"
                "1. <b>Авто-расчет по адресам:</b>\n"
                "   <code>/commute Москва, ул. Лескова 14 | Колледж связи 54 | метро</code>\n"
                "2. <b>Ручной ввод времени (в минутах):</b>\n"
                "   <code>/commute 45</code>"
            )
            return

        home = user.home_address or "Не указан"
        college = user.college_address or "Не указан"
        mode = user.travel_mode or "общественный транспорт"
        await message.answer(
            f"🗺 <b>Ваш текущий маршрут:</b>\n"
            f"🏠 Дом: <b>{home}</b>\n"
            f"🎓 Колледж: <b>{college}</b>\n"
            f"🚇 Способ: <b>{mode}</b>\n"
            f"⏱ Время в пути: <b>{user.commute_minutes} мин</b>\n\n"
            f"Для изменения отправьте: <code>/commute &lt;Дом&gt; | &lt;Колледж&gt; | &lt;транспорт&gt;</code> или <code>/commute 40</code>"
        )
        return

    # Сценарий 2: Прямой ручной ввод минут (например: "/commute 40")
    if args.isdigit():
        mins = int(args)
        user.commute_minutes = mins
        await session.commit()
        await message.answer(f"✅ Время в пути до колледжа установлено: <b>{mins} мин</b>.")
        return

    # Сценарий 3: Автоматический расчет по точкам через "|"
    parts = [p.strip() for p in args.split("|") if p.strip()]
    if len(parts) < 2:
        await message.answer(
            "⚠️ Разделяйте адреса символом <b>|</b>.\n\n"
            "Пример:\n"
            "<code>/commute Москва, метро Бибирево | Москва, Сущевский Вал 5 | метро</code>"
        )
        return

    home = parts[0]
    college = parts[1]
    mode = parts[2] if len(parts) >= 3 else "общественный транспорт"

    status_msg = await message.answer("⏳ Анализирую маршрут и рассчитываю время в пути с учетом утреннего часа пик...")

    res = await calculate_commute(home_address=home, college_address=college, travel_mode=mode)

    if res.get("status") == "error":
        err = res.get("error_message", "Не удалось рассчитать маршрут")
        await status_msg.edit_text(f"❌ Ошибка расчета: {err}")
        return

    mins = res.get("commute_minutes", 40)
    summary = res.get("route_summary", "Оптимальный маршрут")
    traffic_note = res.get("traffic_note", "")

    user.home_address = home
    user.college_address = college
    user.travel_mode = mode
    user.commute_minutes = mins
    await session.commit()

    text = (
        f"🗺 <b>Маршрут успешно рассчитан и сохранен!</b>\n\n"
        f"🏠 <b>Откуда:</b> {home}\n"
        f"🎓 <b>Куда:</b> {college}\n"
        f"🚇 <b>Транспорт:</b> {mode}\n"
        f"⏱ <b>Время в пути:</b> <b>~{mins} мин</b>\n\n"
        f"📍 <b>Маршрут:</b> {summary}\n"
    )
    if traffic_note:
        text += f"\n💡 <i>{traffic_note}</i>"

    await status_msg.edit_text(text)
