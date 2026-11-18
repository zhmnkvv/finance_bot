"""Создание и выборка напоминаний. Отправкой занимается bot/scheduler.py."""

from datetime import datetime, timedelta

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.db.models import Event, Reminder, ReminderKind, Task, User


class ReminderService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _sent_exists(self, *, task_id=None, event_id=None, kind: str, fire_at: datetime) -> bool:
        cond = [Reminder.kind == kind, Reminder.fire_at == fire_at, Reminder.sent_at.is_not(None)]
        cond.append(Reminder.task_id == task_id if task_id else Reminder.event_id == event_id)
        return (await self.session.scalar(select(Reminder.id).where(and_(*cond)).limit(1))) is not None

    async def sync_task(self, user: User, task: Task, now: datetime) -> None:
        """Пересоздаёт неотправленные напоминания задачи под её текущее расписание."""
        await self.session.execute(
            delete(Reminder).where(Reminder.task_id == task.id, Reminder.sent_at.is_(None))
        )
        if task.scheduled_start is None or task.scheduled_end is None:
            return
        before = task.scheduled_start - timedelta(minutes=user.reminder_minutes)
        if before > now and not await self._sent_exists(
            task_id=task.id, kind=ReminderKind.BEFORE_START, fire_at=before
        ):
            self.session.add(
                Reminder(user_id=user.id, task_id=task.id, kind=ReminderKind.BEFORE_START, fire_at=before)
            )
        if not await self._sent_exists(task_id=task.id, kind=ReminderKind.AFTER_END, fire_at=task.scheduled_end):
            self.session.add(
                Reminder(user_id=user.id, task_id=task.id, kind=ReminderKind.AFTER_END, fire_at=task.scheduled_end)
            )

    async def ensure_event(self, user: User, event: Event, now: datetime) -> None:
        before = event.start_time - timedelta(minutes=user.reminder_minutes)
        if before <= now:
            return
        exists = await self.session.scalar(
            select(Reminder.id).where(Reminder.event_id == event.id, Reminder.kind == ReminderKind.BEFORE_START)
        )
        if exists is None:
            self.session.add(
                Reminder(user_id=user.id, event_id=event.id, kind=ReminderKind.BEFORE_START, fire_at=before)
            )

    async def due(self, now: datetime, limit: int = 100) -> list[Reminder]:
        res = await self.session.scalars(
            select(Reminder)
            .where(Reminder.sent_at.is_(None), Reminder.fire_at <= now)
            .order_by(Reminder.fire_at)
            .limit(limit)
        )
        return list(res)
