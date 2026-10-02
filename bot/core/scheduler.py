import logging
from datetime import datetime, timezone
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.core.database import async_session_maker
from bot.database.models import Homework, Lesson, Reminder, User
from bot.services.weather import get_current_weather

logger = logging.getLogger(__name__)


async def check_pending_reminders(bot: Bot) -> None:
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        stmt = (
            select(Reminder, User.telegram_id)
            .join(User, Reminder.user_id == User.id)
            .where(Reminder.trigger_datetime <= now, Reminder.is_sent == False)  # noqa: E712
        )
        res = await session.execute(stmt)
        reminders = res.all()

        for rem, tg_id in reminders:
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=f"⏰ <b>Напоминание:</b>\n{rem.text}",
                )
                rem.is_sent = True
            except Exception as exc:
                logger.error("Failed to send reminder %s to %s: %s", rem.id, tg_id, exc)

        await session.commit()


async def send_morning_digest(bot: Bot) -> None:
    now = datetime.now(timezone.utc)
    weekday = now.isoweekday()
    today_date = now.date()

    async with async_session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()

        for user in users:
            # 1. Погода
            weather_text = ""
            if user.city:
                w_data = await get_current_weather(user.city)
                if w_data and "error" not in w_data:
                    weather_text = (
                        f"🌤 Погода в г. {w_data['city']}: {w_data['temp']}°C, {w_data['description']}.\n"
                        f"🧥 {w_data['advice']}\n\n"
                    )

            # 2. Пары
            les_stmt = (
                select(Lesson)
                .where(Lesson.user_id == user.id, Lesson.day_of_week == weekday)
                .order_by(Lesson.lesson_number)
            )
            lessons = (await session.execute(les_stmt)).scalars().all()
            if lessons:
                les_lines = ["📚 <b>Расписание на сегодня:</b>"]
                for l in lessons:
                    room = f", ауд. {l.room}" if l.room else ""
                    les_lines.append(f"{l.lesson_number}. [{l.start_time} - {l.end_time}] {l.subject}{room}")
                les_text = "\n".join(les_lines) + "\n\n"
            else:
                les_text = "📚 На сегодня пар нет или расписание не заполнено.\n\n"

            # 3. Горящие ДЗ
            hw_stmt = select(Homework).where(
                Homework.user_id == user.id,
                Homework.deadline_date == today_date,
                Homework.is_completed == False,  # noqa: E712
            )
            hws = (await session.execute(hw_stmt)).scalars().all()
            if hws:
                hw_lines = ["🔥 <b>Дедлайны по ДЗ на сегодня:</b>"]
                for hw in hws:
                    hw_lines.append(f"• {hw.subject}: {hw.task_text}")
                hw_text = "\n".join(hw_lines)
            else:
                hw_text = "✨ Горящих дедлайнов на сегодня нет."

            full_msg = f"🌅 <b>Доброе утро! Утренний дайджест:</b>\n\n{weather_text}{les_text}{hw_text}"
            try:
                await bot.send_message(chat_id=user.telegram_id, text=full_msg)
            except Exception as exc:
                logger.error("Morning briefing failed for user %s: %s", user.telegram_id, exc)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_pending_reminders, "interval", seconds=60, args=[bot])
    scheduler.add_job(send_morning_digest, "cron", hour=7, minute=0, args=[bot])
    return scheduler
