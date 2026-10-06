import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select

from bot.core.database import async_session_maker
from bot.database.models import Homework, Lesson, Reminder, SentAlert, User
from bot.services.weather import get_current_weather

logger = logging.getLogger(__name__)


def subtract_minutes_from_hhmm(time_str: str, minutes: int) -> str | None:
    try:
        dt = datetime.strptime(time_str, "%H:%M")
        new_dt = dt - timedelta(minutes=minutes)
        return new_dt.strftime("%H:%M")
    except Exception:
        return None


async def check_pending_reminders(bot: Bot) -> None:
    try:
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
                    logger.error("Failed to send reminder %s: %s", rem.id, exc)

            await session.commit()
    except (asyncio.CancelledError, GeneratorExit):
        return
    except Exception as exc:
        logger.error("Error in check_pending_reminders: %s", exc)


async def check_lesson_alerts(bot: Bot) -> None:
    try:
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
                        alert_hhmm = subtract_minutes_from_hhmm(current_lesson.start_time, 15)
                        if alert_hhmm != current_hhmm:
                            continue

                        # Проверяем персистентный статус в БД
                        check_stmt = select(SentAlert.id).where(
                            SentAlert.user_id == user.id,
                            SentAlert.alert_date == today_str,
                            SentAlert.lesson_id == current_lesson.id,
                            SentAlert.alert_type == "first_lesson",
                        )
                        already_sent = (await session.execute(check_stmt)).scalar_one_or_none()

                        if not already_sent:
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
                                session.add(
                                    SentAlert(
                                        user_id=user.id,
                                        alert_date=today_str,
                                        lesson_id=current_lesson.id,
                                        alert_type="first_lesson",
                                    )
                                )
                                await session.commit()
                            except Exception as exc:
                                logger.error("Failed to send first lesson alert: %s", exc)

                    else:
                        prev_lesson = lessons[idx - 1]
                        alert_hhmm = subtract_minutes_from_hhmm(prev_lesson.end_time, 5)
                        if alert_hhmm != current_hhmm:
                            continue

                        # Проверяем персистентный статус в БД
                        check_stmt = select(SentAlert.id).where(
                            SentAlert.user_id == user.id,
                            SentAlert.alert_date == today_str,
                            SentAlert.lesson_id == current_lesson.id,
                            SentAlert.alert_type == "next_lesson",
                        )
                        already_sent = (await session.execute(check_stmt)).scalar_one_or_none()

                        if not already_sent:
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
                                session.add(
                                    SentAlert(
                                        user_id=user.id,
                                        alert_date=today_str,
                                        lesson_id=current_lesson.id,
                                        alert_type="next_lesson",
                                    )
                                )
                                await session.commit()
                            except Exception as exc:
                                logger.error("Failed to send next lesson alert: %s", exc)
    except (asyncio.CancelledError, GeneratorExit):
        return
    except Exception as exc:
        logger.error("Error in check_lesson_alerts: %s", exc)


async def send_morning_digest(bot: Bot) -> None:
    async with async_session_maker() as session:
        # Очищаем устаревшие записи алертов старше 3 суток
        cleanup_limit = datetime.now(timezone.utc) - timedelta(days=3)
        await session.execute(delete(SentAlert).where(SentAlert.created_at < cleanup_limit))
        await session.commit()

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
                        f"🌤 <b>Погода в г. {w_data['city']}:</b> {w_data['temp']}°C, {w_data['description']}.\n"
                        f"🧥 {w_data['advice']}\n\n"
                    )

            # 2. Пары и расчет времени выхода
            les_stmt = (
                select(Lesson)
                .where(Lesson.user_id == user.id, Lesson.day_of_week == weekday)
                .order_by(Lesson.lesson_number)
            )
            lessons = (await session.execute(les_stmt)).scalars().all()
            commute_text = ""

            if lessons:
                les_lines = ["📚 <b>Расписание на сегодня:</b>"]
                for l in lessons:
                    room = f", ауд. {l.room}" if l.room else ""
                    les_lines.append(f"{l.lesson_number}. [{l.start_time} - {l.end_time}] {l.subject}{room}")
                les_text = "\n".join(les_lines) + "\n\n"

                if user.commute_minutes:
                    first_lesson = lessons[0]
                    total_sub = user.commute_minutes + 10
                    departure = subtract_minutes_from_hhmm(first_lesson.start_time, total_sub)
                    if departure:
                        commute_text = (
                            f"🚪 <b>Время выхода из дома: <u>{departure}</u></b>\n"
                            f"⏱ В пути: ~{user.commute_minutes} мин (+10 мин запас к 1-й паре в {first_lesson.start_time})\n\n"
                        )
            else:
                les_text = "📚 На сегодня пар нет или расписание не заполнено.\n\n"

            # 3. Дедлайны ДЗ
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

            full_msg = f"🌅 <b>Доброе утро! Утренний дайджест:</b>\n\n{weather_text}{commute_text}{les_text}{hw_text}"
            try:
                await bot.send_message(chat_id=user.telegram_id, text=full_msg)
            except Exception as exc:
                logger.error("Morning briefing failed: %s", exc)


async def send_evening_digest(bot: Bot) -> None:
    async with async_session_maker() as session:
        users = (await session.execute(select(User))).scalars().all()

        for user in users:
            tz_str = user.timezone or "Europe/Moscow"
            user_tz = ZoneInfo(tz_str)
            now_local = datetime.now(user_tz)
            tomorrow_date = now_local.date() + timedelta(days=1)
            tomorrow_weekday = (now_local.isoweekday() % 7) + 1

            done_stmt = select(func.count(Homework.id)).where(
                Homework.user_id == user.id,
                Homework.is_completed == True,  # noqa: E712
            )
            done_count = (await session.execute(done_stmt)).scalar() or 0

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
                logger.error("Evening briefing failed: %s", exc)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_pending_reminders, "interval", seconds=60, args=[bot])
    scheduler.add_job(check_lesson_alerts, "interval", seconds=60, args=[bot])
    scheduler.add_job(send_morning_digest, "cron", hour=7, minute=0, args=[bot])
    scheduler.add_job(send_evening_digest, "cron", hour=20, minute=0, args=[bot])
    return scheduler
