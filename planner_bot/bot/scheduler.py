"""Фоновые задачи: напоминания, утренний план, вечерние итоги.

Всё состояние — в БД (таблица reminders и поля last_*_sent у пользователя),
поэтому после перезапуска бот ничего не теряет и не дублирует.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner_bot.bot import formatting as fmt
from planner_bot.bot import keyboards as kb
from planner_bot.bot.notifier import safe_send
from planner_bot.db.models import OPEN_STATUSES, Event, Reminder, ReminderKind, Task, User
from planner_bot.services.analytics import AnalyticsService
from planner_bot.services.planner import PlannerService
from planner_bot.services.reminders import ReminderService
from planner_bot.utils.dates import get_tz, local_dt, now_utc

log = logging.getLogger(__name__)

# Напоминание «скоро начало», опоздавшее сильнее этого (бот был выключен), уже бесполезно
STALE_BEFORE_START = timedelta(minutes=30)
# Утренний план/итоги шлём только в этом окне после заданного времени
DIGEST_WINDOW = timedelta(hours=3)


async def run_scheduler(bot: Bot, factory: async_sessionmaker[AsyncSession], interval: int) -> None:
    log.info("scheduler started, interval=%ss", interval)
    while True:
        try:
            now = now_utc()
            await process_reminders(bot, factory, now)
            await process_digests(bot, factory, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


async def process_reminders(bot: Bot, factory: async_sessionmaker[AsyncSession], now: datetime) -> None:
    async with factory() as session:
        for r in await ReminderService(session).due(now):
            # Помечаем до отправки: лучше потерять одно напоминание, чем слать его в цикле
            r.sent_at = now
            await session.commit()
            try:
                await _send_reminder(bot, session, r, now)
            except Exception:
                log.exception("failed to send reminder %s", r.id)
            await session.commit()


async def _send_reminder(bot: Bot, session: AsyncSession, r: Reminder, now: datetime) -> None:
    user = await session.get(User, r.user_id)
    if user is None or not user.is_active or not user.notifications_enabled:
        return
    tz = get_tz(user.timezone)

    if r.task_id is not None:
        task = await session.get(Task, r.task_id)
        if task is None or task.status not in OPEN_STATUSES:
            return
        if r.kind == ReminderKind.BEFORE_START:
            if now - r.fire_at > STALE_BEFORE_START or task.scheduled_start is None:
                return
            minutes = max(1, round((task.scheduled_start - now).total_seconds() / 60))
            text = (
                f"⏰ <b>Через {minutes} мин:</b>\n"
                f"{fmt.emoji_for(task.title)} {escape(task.title)} "
                f"({fmt.hm(task.scheduled_start, tz)}–{fmt.hm(task.scheduled_end, tz)})\n\nНачать сейчас?"
            )
            markup = kb.before_start_kb(task.id)
        else:
            if task.scheduled_end != r.fire_at:
                return  # задачу уже перенесли
            text = f"Как прошло?\n\n{fmt.emoji_for(task.title)} <b>{escape(task.title)}</b>"
            markup = kb.after_end_kb(task.id)
        user.context_task_id = task.id
        await safe_send(bot, user, text, markup)
        return

    if r.event_id is not None:
        event = await session.get(Event, r.event_id)
        if event is None or now - r.fire_at > STALE_BEFORE_START:
            return
        minutes = max(1, round((event.start_time - now).total_seconds() / 60))
        text = (
            f"⏰ <b>Через {minutes} мин:</b>\n{fmt.emoji_for(event.title, 'event')} {escape(event.title)} "
            f"({fmt.hm(event.start_time, tz)}–{fmt.hm(event.end_time, tz)})"
        )
        await safe_send(bot, user, text)


def _due(local_now: datetime, at, last_sent, tz) -> tuple[bool, bool]:
    """(пора ли слать, пропущено ли окно). Шлём один раз в день в окне DIGEST_WINDOW."""
    today = local_now.date()
    if last_sent == today:
        return False, False
    target = local_dt(today, at, tz)
    if local_now < target:
        return False, False
    return (local_now - target) <= DIGEST_WINDOW, (local_now - target) > DIGEST_WINDOW


async def process_digests(bot: Bot, factory: async_sessionmaker[AsyncSession], now: datetime) -> None:
    async with factory() as session:
        users = list(
            await session.scalars(select(User).where(User.is_active.is_(True), User.notifications_enabled.is_(True)))
        )
        for user in users:
            try:
                await _user_digests(bot, session, user, now)
                await session.commit()
            except Exception:
                await session.rollback()
                log.exception("digest failed for user %s", user.id)


async def _user_digests(bot: Bot, session: AsyncSession, user: User, now: datetime) -> None:
    tz = get_tz(user.timezone)
    local_now = now.astimezone(tz)
    today = local_now.date()

    send_morning, missed = _due(local_now, user.morning_time, user.last_morning_sent, tz)
    if send_morning:
        plan = await PlannerService(session).plan_day(user, today, now, persist=True)
        await safe_send(bot, user, fmt.format_morning(plan, tz, user))
        user.last_morning_sent = today
    elif missed:
        user.last_morning_sent = today

    send_evening, missed = _due(local_now, user.evening_time, user.last_evening_sent, tz)
    if send_evening:
        summary = await AnalyticsService(session).day_summary(user, today)
        if summary.total or summary.events:
            markup = kb.summary_kb() if summary.not_done else None
            await safe_send(bot, user, fmt.format_summary(summary, tz), markup)
        user.last_evening_sent = today
    elif missed:
        user.last_evening_sent = today
