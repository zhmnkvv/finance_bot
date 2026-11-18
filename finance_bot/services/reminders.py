"""Напоминания о платежах с повтором (однократно / еженедельно / ежемесячно) в поясе пользователя."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Reminder, ReminderRepeat, User
from finance_bot.schemas.finance import ReminderCreate
from finance_bot.utils.dates import add_months, get_tz, local_dt

REMINDER_KINDS = {
    "internet": ("🌐", "Оплатить интернет"),
    "rent": ("🏠", "Оплатить аренду"),
    "subscription": ("📺", "Оплатить подписку"),
    "debt": ("💳", "Вернуть долг"),
    "salary": ("💼", "Получить зарплату"),
    "other": ("🔔", "Другой платёж"),
}
# Подходящая категория расхода/дохода для кнопки «записать операцию»
KIND_TO_CATEGORY = {
    "internet": ("expense", "subscriptions"),
    "rent": ("expense", "housing"),
    "subscription": ("expense", "subscriptions"),
    "debt": ("expense", "transfers"),
    "salary": ("income", "salary"),
    "other": ("expense", "other"),
}


class ReminderError(ValueError):
    pass


def next_occurrence(local_date: date, rem_repeat: str, anchor_day: int) -> date:
    if rem_repeat == ReminderRepeat.WEEKLY:
        return local_date + timedelta(days=7)
    if rem_repeat == ReminderRepeat.MONTHLY:
        return add_months(local_date, 1, anchor_day)
    raise ValueError("one-time reminder has no next occurrence")


def first_run(data_date: date, rem: Reminder, tz: ZoneInfo, now: datetime) -> datetime:
    """Первый момент в будущем; для повторяющихся пропускаем прошедшие даты."""
    d = data_date
    run = local_dt(d, rem.local_time, tz)
    while run <= now and rem.repeat != ReminderRepeat.NONE:
        d = next_occurrence(d, rem.repeat, rem.anchor_day)
        run = local_dt(d, rem.local_time, tz)
    return run


class ReminderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user: User, reminder_id: int) -> Reminder | None:
        return await self.session.scalar(
            select(Reminder).where(Reminder.id == reminder_id, Reminder.user_id == user.id)
        )

    async def list(self, user: User) -> list[Reminder]:
        res = await self.session.scalars(
            select(Reminder).where(Reminder.user_id == user.id, Reminder.is_active.is_(True)).order_by(Reminder.next_run_at)
        )
        return list(res)

    async def create(self, user: User, data: ReminderCreate, now: datetime) -> tuple[Reminder, bool]:
        if data.client_token:
            existing = await self.session.scalar(
                select(Reminder).where(Reminder.user_id == user.id, Reminder.client_token == data.client_token)
            )
            if existing is not None:
                return existing, False
        tz = get_tz(user.settings.timezone)
        anchor = data.first_date.weekday() if data.repeat == ReminderRepeat.WEEKLY else data.first_date.day
        rem = Reminder(
            user_id=user.id,
            kind=data.kind,
            title=data.title,
            amount=data.amount,
            currency=data.currency,
            repeat=str(data.repeat),
            local_time=data.local_time,
            anchor_day=anchor,
            debt_id=data.debt_id,
            client_token=data.client_token,
            is_active=True,
        )
        run = first_run(data.first_date, rem, tz, now)
        if run <= now:
            raise ReminderError("Это время уже прошло. Укажи дату и время в будущем.")
        rem.next_run_at = run.astimezone(UTC)
        self.session.add(rem)
        await self.session.flush()
        return rem, True

    async def delete(self, user: User, rem: Reminder) -> None:
        if rem.user_id != user.id:
            raise ReminderError("Напоминание не найдено.")
        await self.session.delete(rem)
        await self.session.flush()

    async def due(self, now: datetime, limit: int = 100) -> list[Reminder]:
        res = await self.session.scalars(
            select(Reminder)
            .where(Reminder.is_active.is_(True), Reminder.next_run_at <= now)
            .order_by(Reminder.next_run_at)
            .limit(limit)
        )
        return list(res)

    def advance(self, rem: Reminder, tz: ZoneInfo, now: datetime) -> None:
        """После отправки: однократное выключаем, повторяющееся переносим на следующий период."""
        rem.last_sent_at = now
        if rem.repeat == ReminderRepeat.NONE:
            rem.is_active = False
            return
        d = rem.next_run_at.astimezone(tz).date()
        run = rem.next_run_at
        while run <= now:
            d = next_occurrence(d, rem.repeat, rem.anchor_day)
            run = local_dt(d, rem.local_time, tz)
        rem.next_run_at = run.astimezone(UTC)

    async def snooze(self, user: User, rem: Reminder, now: datetime, minutes: int = 60) -> None:
        if rem.user_id != user.id:
            raise ReminderError("Напоминание не найдено.")
        rem.is_active = True
        rem.next_run_at = now + timedelta(minutes=minutes)
        await self.session.flush()

    async def retime_for_timezone(self, user: User, old_tz: ZoneInfo, new_tz: ZoneInfo) -> None:
        """Сменился пояс — сохраняем локальное время напоминаний («10:00 по-местному»)."""
        for rem in await self.list(user):
            local_date = rem.next_run_at.astimezone(old_tz).date()
            rem.next_run_at = local_dt(local_date, rem.local_time, new_tz).astimezone(UTC)
        await self.session.flush()
