"""Связка БД и алгоритма планирования: план дня, перенос задач."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.db.models import Priority, Task, TaskStatus, TimeWindow, User
from planner_bot.schemas.commands import RescheduleSpec
from planner_bot.services.events import EventService
from planner_bot.services.reminders import ReminderService
from planner_bot.services.scheduling import (
    DEFAULT_TASK_MINUTES,
    BusyBlock,
    DayPlan,
    Interval,
    SchedTask,
    build_day_plan,
    conflicts,
    find_slot,
    task_sort_key,
)
from planner_bot.services.tasks import TaskService
from planner_bot.utils.dates import get_tz, local_dt, now_utc

log = logging.getLogger(__name__)


class RescheduleError(Exception):
    """Ошибка переноса с понятным пользователю текстом."""


@dataclass
class RescheduleResult:
    task: Task
    day: date
    start: datetime | None
    end: datetime | None
    adjusted: bool = False  # время сдвинули из-за пересечения с событием


def to_sched(task: Task) -> SchedTask:
    return SchedTask(
        id=task.id,
        title=task.title,
        priority=Priority(task.priority),
        deadline=task.deadline,
        due_date=task.due_date,
        estimated_minutes=task.estimated_minutes,
        time_window=TimeWindow(task.time_window) if task.time_window else None,
        not_before=task.not_before,
    )


class PlannerService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.tasks = TaskService(session)
        self.events = EventService(session)
        self.reminders = ReminderService(session)

    async def _split_tasks(self, user: User, day: date, now: datetime) -> tuple[list[Task], list[Task]]:
        """Делит открытые задачи на закреплённые по времени (busy) и гибкие для этого дня."""
        tz = get_tz(user.timezone)
        today = now.astimezone(tz).date()
        fixed, flexible = [], []
        for t in await self.tasks.list_open(user, limit=500):
            start_day = t.scheduled_start.astimezone(tz).date() if t.scheduled_start else None
            if t.is_time_fixed and start_day is not None and t.scheduled_end is not None:
                if start_day == day and (day != today or t.scheduled_end > now):
                    fixed.append(t)
                elif day == today and (start_day < today or t.scheduled_end <= now):
                    # Закреплённое время прошло, а задача не выполнена — планируем заново
                    flexible.append(t)
                continue
            in_progress = (
                day == today
                and t.scheduled_start is not None
                and t.scheduled_end is not None
                and t.scheduled_start <= now < t.scheduled_end
            )
            if in_progress:
                fixed.append(t)
                continue
            if t.not_before is not None and t.not_before.astimezone(tz).date() > day:
                continue
            if t.due_date is not None:
                if t.due_date == day or (day == today and t.due_date < today):
                    flexible.append(t)
                continue
            if day >= today:
                flexible.append(t)
        return fixed, flexible

    async def busy_blocks(self, user: User, day: date, now: datetime, exclude_task: int | None = None):
        events = await self.events.list_for_day(user, day)
        fixed, _ = await self._split_tasks(user, day, now)
        busy = [BusyBlock(e.start_time, e.end_time, e.title, event_id=e.id) for e in events]
        busy += [
            BusyBlock(t.scheduled_start, t.scheduled_end, t.title, task_id=t.id)
            for t in fixed
            if t.id != exclude_task and t.scheduled_start and t.scheduled_end
        ]
        return busy, events

    async def plan_day(
        self, user: User, day: date, now: datetime | None = None, persist: bool | None = None
    ) -> DayPlan:
        now = now or now_utc()
        tz = get_tz(user.timezone)
        today = now.astimezone(tz).date()
        if persist is None:
            persist = day == today

        busy, events = await self.busy_blocks(user, day, now)
        _, flexible = await self._split_tasks(user, day, now)
        plan = build_day_plan(
            day=day,
            tz=tz,
            now=now,
            day_start=user.day_start,
            day_end=user.day_end,
            busy=busy,
            tasks=[to_sched(t) for t in flexible],
        )
        # Главная задача — самая срочная/важная из всех задач дня, включая закреплённые по времени
        fixed, _ = await self._split_tasks(user, day, now)
        day_tasks = [to_sched(t) for t in fixed + flexible]
        plan.main_task = min(day_tasks, key=lambda t: task_sort_key(t, day, tz)) if day_tasks else None
        if persist:
            await self._persist(user, plan, flexible, events, now)
        return plan

    async def _persist(self, user: User, plan: DayPlan, flexible: list[Task], events, now: datetime) -> None:
        for task in flexible:
            slot = plan.slot_for(task.id)
            if slot is None:
                if task.scheduled_start is not None:
                    await self.tasks.unschedule(task)
                continue
            if task.scheduled_start != slot.start or task.scheduled_end != slot.end:
                task.scheduled_start, task.scheduled_end = slot.start, slot.end
                task.is_time_fixed = False
            task.status = TaskStatus.SCHEDULED
            await self.reminders.sync_task(user, task, now)
        for ev in events:
            await self.reminders.ensure_event(user, ev, now)
        await self.session.flush()

    async def reschedule(
        self, user: User, task: Task, spec: RescheduleSpec, now: datetime | None = None
    ) -> RescheduleResult:
        now = now or now_utc()
        tz = get_tz(user.timezone)
        today = now.astimezone(tz).date()

        day = spec.day
        if day is None:
            t = spec.exact_time or spec.after_time
            day = today if (t is not None and local_dt(today, t, tz) > now) else today + timedelta(days=1)
        if day < today:
            raise RescheduleError("Нельзя перенести задачу в прошлое.")

        if spec.exact_time is not None:
            start = local_dt(day, spec.exact_time, tz)
            if start <= now:
                raise RescheduleError("Это время уже прошло. Выбери время позже.")
            minutes = task.estimated_minutes or DEFAULT_TASK_MINUTES
            candidate = Interval(start, start + timedelta(minutes=minutes))
            busy, _ = await self.busy_blocks(user, day, now, exclude_task=task.id)
            adjusted = False
            if conflicts(candidate, busy):
                window = Interval(start, local_dt(day, user.day_end, tz))
                slot = find_slot([Interval(b.start, b.end) for b in busy], window, minutes)
                if slot is None:
                    raise RescheduleError("В это время уже есть дела, а свободного окна позже в этот день нет.")
                candidate, adjusted = slot, True
            task.scheduled_start, task.scheduled_end = candidate.start, candidate.end
            task.is_time_fixed = True
            task.due_date = day
            task.not_before = None
            task.status = TaskStatus.SCHEDULED
            await self.reminders.sync_task(user, task, now)
            await self.session.flush()
            return RescheduleResult(task, day, candidate.start, candidate.end, adjusted)

        await self.tasks.unschedule(task)
        task.due_date = day
        task.not_before = local_dt(day, spec.after_time, tz) if spec.after_time else None
        await self.session.flush()

        plan = await self.plan_day(user, day, now, persist=(day == today))
        slot = plan.slot_for(task.id)
        return RescheduleResult(task, day, slot.start if slot else None, slot.end if slot else None)

    async def move_open_to_tomorrow(self, user: User, now: datetime | None = None) -> list[Task]:
        """Незавершённые задачи сегодняшнего дня → на завтра."""
        now = now or now_utc()
        tz = get_tz(user.timezone)
        today = now.astimezone(tz).date()
        tomorrow = today + timedelta(days=1)
        moved = []
        for t in await self.tasks.list_open(user, limit=500):
            start_day = t.scheduled_start.astimezone(tz).date() if t.scheduled_start else None
            if start_day == today or (t.due_date is not None and t.due_date <= today):
                await self.tasks.unschedule(t)
                t.due_date = tomorrow
                t.not_before = None
                moved.append(t)
        await self.session.flush()
        return moved
