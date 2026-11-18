"""Итоги дня и статистика."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.db.models import OPEN_STATUSES, Event, Task, TaskStatus, User
from planner_bot.services.events import EventService
from planner_bot.utils.dates import day_bounds_utc, get_tz


@dataclass
class DaySummary:
    day: date
    done: list[Task] = field(default_factory=list)
    not_done: list[Task] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.done) + len(self.not_done)


@dataclass
class Stats:
    done_today: int
    planned_today: int
    done_week: int
    open_total: int
    overdue: int


class AnalyticsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def day_summary(self, user: User, day: date) -> DaySummary:
        tz = get_tz(user.timezone)
        start, end = day_bounds_utc(day, tz)
        done = list(
            await self.session.scalars(
                select(Task)
                .where(
                    Task.user_id == user.id,
                    Task.status == TaskStatus.DONE,
                    Task.completed_at >= start,
                    Task.completed_at < end,
                )
                .order_by(Task.completed_at)
            )
        )
        open_tasks = await self.session.scalars(
            select(Task).where(Task.user_id == user.id, Task.status.in_(OPEN_STATUSES)).order_by(Task.id)
        )
        not_done = [
            t
            for t in open_tasks
            if (t.scheduled_start is not None and start <= t.scheduled_start < end)
            or (t.due_date is not None and t.due_date <= day)
        ]
        events = await EventService(self.session).list_for_day(user, day)
        return DaySummary(day=day, done=done, not_done=not_done, events=events)

    async def stats(self, user: User, now: datetime) -> Stats:
        tz = get_tz(user.timezone)
        today = now.astimezone(tz).date()
        summary = await self.day_summary(user, today)
        week_start, _ = day_bounds_utc(today - timedelta(days=6), tz)
        done_week = await self.session.scalar(
            select(func.count(Task.id)).where(
                Task.user_id == user.id, Task.status == TaskStatus.DONE, Task.completed_at >= week_start
            )
        )
        open_total = await self.session.scalar(
            select(func.count(Task.id)).where(Task.user_id == user.id, Task.status.in_(OPEN_STATUSES))
        )
        overdue = await self.session.scalar(
            select(func.count(Task.id)).where(
                Task.user_id == user.id, Task.status.in_(OPEN_STATUSES), Task.deadline < now
            )
        )
        return Stats(
            done_today=len(summary.done),
            planned_today=summary.total,
            done_week=done_week or 0,
            open_total=open_total or 0,
            overdue=overdue or 0,
        )
