"""Фоновые задачи: напоминания о платежах и о сроках долгов.

Всё состояние — в БД (next_run_at, last_sent_at, last_reminded_on), поэтому после
перезапуска бот ничего не теряет и не присылает одно напоминание дважды.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.notifier import safe_send
from finance_bot.db.models import Reminder, ReminderRepeat, User
from finance_bot.services.debts import DebtService
from finance_bot.services.reminders import ReminderService
from finance_bot.utils.dates import get_tz, local_dt, now_utc

log = logging.getLogger(__name__)

# Если бот был выключен дольше этого, однократное напоминание всё равно отправим, но с пометкой
LATE_THRESHOLD = timedelta(hours=1)


async def run_scheduler(bot: Bot, factory: async_sessionmaker[AsyncSession], interval: int) -> None:
    log.info("scheduler started, interval=%ss", interval)
    while True:
        try:
            now = now_utc()
            await process_reminders(bot, factory, now)
            await process_debt_reminders(bot, factory, now)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)


def reminder_kb(rem: Reminder):
    rows = []
    if rem.amount:
        label = "➕ Записать доход" if rem.kind == "salary" else "➖ Записать расход"
        rows.append([kb.btn(label, kb.RemCb(action="record", id=rem.id))])
    second = [kb.btn("✅ Готово", kb.RemCb(action="done", id=rem.id))]
    if rem.repeat == ReminderRepeat.NONE:
        second.append(kb.btn("⏰ Через час", kb.RemCb(action="snooze", id=rem.id)))
    rows.append(second)
    return kb.markup(rows)


async def process_reminders(bot: Bot, factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    sent = 0
    async with factory() as session:
        svc = ReminderService(session)
        for rem in await svc.due(now):
            user = await session.get(User, rem.user_id)
            tz = get_tz(user.settings.timezone) if user and user.settings else get_tz("UTC")
            late = now - rem.next_run_at > LATE_THRESHOLD
            # Сначала сдвигаем расписание и коммитим: лучше пропустить одно, чем слать в цикле
            svc.advance(rem, tz, now)
            await session.commit()
            if user is None or not user.is_active or not user.settings.notifications_enabled:
                continue
            text = texts.reminder_fire(rem) + ("\n\n<i>(с опозданием — бот был недоступен)</i>" if late else "")
            if await safe_send(bot, user, text, reminder_kb(rem)):
                sent += 1
            await session.commit()
    return sent


async def process_debt_reminders(bot: Bot, factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    sent = 0
    async with factory() as session:
        users = list(await session.scalars(select(User).where(User.is_active.is_(True))))
        for user in users:
            s = user.settings
            if s is None or not s.notifications_enabled:
                continue
            tz = get_tz(s.timezone)
            today = now.astimezone(tz).date()
            if now < local_dt(today, s.debt_reminder_time, tz):
                continue
            for debt, days_left in await DebtService(session).due_for_reminder(user, today):
                debt.last_reminded_on = today
                await session.commit()
                markup = kb.markup([[kb.btn("Открыть долг", kb.DebtCb(action="open", id=debt.id))]])
                if await safe_send(bot, user, texts.debt_due_notice(debt, days_left), markup):
                    sent += 1
            await session.commit()
    return sent
