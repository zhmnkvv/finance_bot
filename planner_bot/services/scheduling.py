"""Алгоритм построения плана дня. Чистые функции без БД и Telegram.

Идея: фиксированные события (и задачи со временем, выбранным пользователем) —
это занятые интервалы. Остальные задачи сортируются по срочности/приоритету
и жадно раскладываются в первые подходящие свободные промежутки.
Задачи никогда не ставятся поверх занятых интервалов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from planner_bot.db.models import Priority, TimeWindow
from planner_bot.utils.dates import end_of_day, local_dt, round_up

DEFAULT_TASK_MINUTES = 60
PRIORITY_WEIGHT = {Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}

# Границы времени суток для time_window (локальное время)
WINDOW_BOUNDS = {
    TimeWindow.MORNING: (time(6, 0), time(12, 0)),
    TimeWindow.AFTERNOON: (time(12, 0), time(18, 0)),
    TimeWindow.EVENING: (time(18, 0), time(23, 59)),
}


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class BusyBlock:
    """Фиксированный интервал: событие или задача с закреплённым временем."""

    start: datetime
    end: datetime
    title: str
    event_id: int | None = None
    task_id: int | None = None


@dataclass
class SchedTask:
    id: int
    title: str
    priority: Priority = Priority.MEDIUM
    deadline: datetime | None = None
    due_date: date | None = None
    estimated_minutes: int | None = None
    time_window: TimeWindow | None = None
    not_before: datetime | None = None


@dataclass
class PlanItem:
    start: datetime
    end: datetime
    title: str
    kind: str  # "event" | "task" | "break"
    task_id: int | None = None
    event_id: int | None = None
    priority: Priority | None = None
    overdue: bool = False


@dataclass
class DayPlan:
    day: date
    items: list[PlanItem] = field(default_factory=list)
    unscheduled: list[SchedTask] = field(default_factory=list)
    main_task: SchedTask | None = None

    @property
    def task_count(self) -> int:
        return sum(1 for i in self.items if i.kind == "task") + len(self.unscheduled)

    def slot_for(self, task_id: int) -> PlanItem | None:
        return next((i for i in self.items if i.task_id == task_id), None)


def free_slots(busy: list[Interval], window: Interval) -> list[Interval]:
    """Свободные промежутки внутри window за вычетом занятых интервалов."""
    slots: list[Interval] = []
    cursor = window.start
    for b in sorted(busy, key=lambda i: i.start):
        if b.end <= cursor or b.start >= window.end:
            continue
        if b.start > cursor:
            slots.append(Interval(cursor, min(b.start, window.end)))
        cursor = max(cursor, b.end)
        if cursor >= window.end:
            break
    if cursor < window.end:
        slots.append(Interval(cursor, window.end))
    return [s for s in slots if s.end > s.start]


def find_slot(busy: list[Interval], window: Interval, minutes: int) -> Interval | None:
    for slot in free_slots(busy, window):
        if slot.minutes >= minutes:
            return Interval(slot.start, slot.start + timedelta(minutes=minutes))
    return None


def task_sort_key(task: SchedTask, day: date, tz: ZoneInfo):
    """Сначала то, что горит сегодня, потом по приоритету, затем по дедлайну."""
    eod = end_of_day(day, tz)
    burning = 0 if (task.deadline is not None and task.deadline <= eod) else 1
    pinned_to_day = 0 if task.due_date is not None and task.due_date <= day else 1
    deadline_ts = task.deadline.timestamp() if task.deadline else float("inf")
    return (burning, -PRIORITY_WEIGHT[task.priority], pinned_to_day, deadline_ts, task.id)


def build_day_plan(
    *,
    day: date,
    tz: ZoneInfo,
    now: datetime,
    day_start: time,
    day_end: time,
    busy: list[BusyBlock],
    tasks: list[SchedTask],
    lunch: tuple[time, time] | None = (time(13, 0), time(14, 0)),
) -> DayPlan:
    plan = DayPlan(day=day)
    window = Interval(local_dt(day, day_start, tz), local_dt(day, day_end, tz))
    if now.astimezone(tz).date() == day:
        window = Interval(max(window.start, round_up(now, 5)), window.end)

    occupied: list[Interval] = []
    for b in sorted(busy, key=lambda x: x.start):
        occupied.append(Interval(b.start, b.end))
        kind = "event" if b.event_id is not None else "task"
        plan.items.append(PlanItem(b.start, b.end, b.title, kind, task_id=b.task_id, event_id=b.event_id))

    if lunch is not None:
        lunch_iv = Interval(local_dt(day, lunch[0], tz), local_dt(day, lunch[1], tz))
        if lunch_iv.start >= window.start and lunch_iv.end <= window.end and not any(
            lunch_iv.overlaps(o) for o in occupied
        ):
            occupied.append(lunch_iv)
            plan.items.append(PlanItem(lunch_iv.start, lunch_iv.end, "Обед", "break"))

    ordered = sorted(tasks, key=lambda t: task_sort_key(t, day, tz))
    for task in ordered:
        minutes = task.estimated_minutes or DEFAULT_TASK_MINUTES
        task_window = window
        if task.time_window is not None:
            ws, we = WINDOW_BOUNDS[task.time_window]
            task_window = Interval(
                max(window.start, local_dt(day, ws, tz)), min(window.end, local_dt(day, we, tz))
            )
        if task.not_before is not None and task.not_before > task_window.start:
            task_window = Interval(task.not_before, task_window.end)

        slot = find_slot(occupied, task_window, minutes) if task_window.end > task_window.start else None
        if slot is None:
            plan.unscheduled.append(task)
            continue
        occupied.append(slot)
        plan.items.append(
            PlanItem(
                slot.start,
                slot.end,
                task.title,
                "task",
                task_id=task.id,
                priority=task.priority,
                overdue=task.deadline is not None and task.deadline < slot.end,
            )
        )

    plan.items.sort(key=lambda i: i.start)
    plan.main_task = ordered[0] if ordered else None
    return plan


def conflicts(candidate: Interval, busy: list[BusyBlock]) -> list[BusyBlock]:
    return [b for b in busy if candidate.overlaps(Interval(b.start, b.end))]
