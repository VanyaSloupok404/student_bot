import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from bot.core.database import async_session_maker
from bot.database.models import Homework, Lesson, Reminder, User
from bot.services.weather import get_current_weather

logger = logging.getLogger(__name__)

# Кэш отправленных алертов за текущие сутки: (user_id, date_str, lesson_id, alert_type)
sent_alerts_cache: set[tuple[int, str, int, str]] = set()


def subtract_minutes_from_hhmm(time_str: str, minutes: int) -> str | None:
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        new_dt = dt - timedelta(minutes=minutes)
        return new_dt.strftime("%H:%M")
    except Exception:
        return None


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


async def check_lesson_alerts(bot: Bot) -> None:
    async with async_session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()

        for user in users:
            tz_str = user.timezone or "Europe/Moscow"
            try:
                user_tz = ZoneInfo(tz_str)
            except Exception:
                user_tz = ZoneInfo("Europe/Moscow")

            now_local = datetime.now(user_tz)
            today_str = now_local.strftime("%Y-%m-%d")
            current_hhmm = now_local.strftime("%H:%M")
            weekday = now_local.isoweekday()

            # Получаем пары на сегодня по порядку
            stmt = (
                select(Lesson)
                .where(Lesson.user_id == user.id, Lesson.day_of_week == weekday)
                .order_by(Lesson.lesson_number)
            )
            lessons = (await session.execute(stmt)).scalars().all()
            if not lessons:
                continue

            for idx, current_lesson in enumerate(lessons):
                if idx == 0:
                    # Первая пара дня: оповещение за 15 минут до ее начала
                    alert_hhmm = subtract_minutes_from_hhmm(current_lesson.start_time, 15)
                    cache_key = (user.id, today_str, current_lesson.id, "first_lesson")

                    if alert_hhmm == current_hhmm and cache_key not in sent_alerts_cache:
                        room = f"\n📍 Кабинет: {current_lesson.room}" if current_lesson.room else ""
                        teacher = f"\n👨‍🏫 Преподаватель: {current_lesson.teacher}" if current_lesson.teacher else ""
                        msg = (
                            f"🔔 <b>Через 15 минут начинается первая пара!</b>\n\n"
                            f"📚 <b>{current_lesson.lesson_number} пара:</b> {current_lesson.subject}\n"
                            f"⏰ Время: {current_lesson.start_time} - {current_lesson.end_time}"
                            f"{room}{teacher}"
                        )
                        try:
                            await bot.send_message(chat_id=user.telegram_id, text=msg)
                            sent_alerts_cache.add(cache_key)
                        except Exception as exc:
                            logger.error("Failed to send first lesson alert to %s: %s", user.telegram_id, exc)

                else:
                    # Последующие пары: оповещение строго за 5 минут до окончания предыдущей пары
                    prev_lesson = lessons[idx - 1]
                    alert_hhmm = subtract_minutes_from_hhmm(prev_lesson.end_time, 5)
                    cache_key = (user.id, today_str, current_lesson.id, "next_lesson")

                    if alert_hhmm == current_hhmm and cache_key not in sent_alerts_cache:
                        room = f"\n📍 Кабинет: <b>{current_lesson.room}</b>" if current_lesson.room else ""
                        teacher = f"\n👨‍🏫 Преподаватель: {current_lesson.teacher}" if current_lesson.teacher else ""
                        msg = (
                            f"🔔 <b>Скоро следующая пара!</b>\n"
                            f"<i>(Уведомление за 5 мин до конца текущей пары)</i>\n\n"
                            f"📚 <b>{current_lesson.lesson_number} пара:</b> {current_lesson.subject}\n"
                            f"⏰ Начало: <b>{current_lesson.start_time}</b> (до {current_lesson.end_time})\n"
                            f"⏳ Заканчивается: {prev_lesson.subject} (в {prev_lesson.end_time})"
                            f"{room}{teacher}"
                        )
                        try:
                            await bot.send_message(chat_id=user.telegram_id, text=msg)
                            sent_alerts_cache.add(cache_key)
                        except Exception as exc:
                            logger.error("Failed to send next lesson alert to %s: %s", user.telegram_id, exc)


async def send_morning_digest(bot: Bot) -> None:
    async with async_session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()

        for user in users:
            tz_str = user.timezone or "Europe/Moscow"
            user_tz = ZoneInfo(tz_str)
            now_local = datetime.now(user_tz)
            weekday = now_local.isoweekday()
            today_date = now_local.date()

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


async def send_evening_digest(bot: Bot) -> None:
    async with async_session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()

        for user in users:
            tz_str = user.timezone or "Europe/Moscow"
            user_tz = ZoneInfo(tz_str)
            now_local = datetime.now(user_tz)
            tomorrow_date = now_local.date() + timedelta(days=1)
            tomorrow_weekday = (now_local.isoweekday() % 7) + 1

            # 1. Статистика выполненных ДЗ
            done_stmt = select(func.count(Homework.id)).where(
                Homework.user_id == user.id,
                Homework.is_completed == True,  # noqa: E712
            )
            done_count = (await session.execute(done_stmt)).scalar() or 0

            # 2. Пары на завтра
            stmt = (
                select(Lesson)
                .where(Lesson.user_id == user.id, Lesson.day_of_week == tomorrow_weekday)
                .order_by(Lesson.lesson_number)
            )
            tomorrow_lessons = (await session.execute(stmt)).scalars().all()

            if tomorrow_lessons:
                les_lines = ["📚 <b>Расписание на завтра:</b>"]
                for l in tomorrow_lessons:
                    room = f", ауд. {l.room}" if l.room else ""
                    les_lines.append(f"{l.lesson_number}. [{l.start_time} - {l.end_time}] {l.subject}{room}")
                tomorrow_schedule_text = "\n".join(les_lines) + "\n\n"
            else:
                tomorrow_schedule_text = "📚 На завтра пар нет в расписании.\n\n"

            # 3. Дедлайны ДЗ на завтра
            hw_stmt = select(Homework).where(
                Homework.user_id == user.id,
                Homework.deadline_date == tomorrow_date,
                Homework.is_completed == False,  # noqa: E712
            )
            tomorrow_hws = (await session.execute(hw_stmt)).scalars().all()
            if tomorrow_hws:
                hw_lines = ["📝 <b>Дедлайны на завтра:</b>"]
                for hw in tomorrow_hws:
                    hw_lines.append(f"• {hw.subject}: {hw.task_text}")
                tomorrow_hw_text = "\n".join(hw_lines)
            else:
                tomorrow_hw_text = "✨ Горящих дедлайнов на завтра нет."

            full_msg = (
                f"🌙 <b>Вечерний дайджест (20:00):</b>\n\n"
                f"🏆 Всего выполнено задач: <b>{done_count}</b>\n\n"
                f"{tomorrow_schedule_text}"
                f"{tomorrow_hw_text}"
            )
            try:
                await bot.send_message(chat_id=user.telegram_id, text=full_msg)
            except Exception as exc:
                logger.error("Evening briefing failed for user %s: %s", user.telegram_id, exc)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_pending_reminders, "interval", seconds=60, args=[bot])
    scheduler.add_job(check_lesson_alerts, "interval", seconds=60, args=[bot])
    scheduler.add_job(send_morning_digest, "cron", hour=7, minute=0, args=[bot])
    scheduler.add_job(send_evening_digest, "cron", hour=20, minute=0, args=[bot])
    return scheduler
