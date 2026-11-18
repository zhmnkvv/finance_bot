"""Алгоритм планирования: свободные окна, пересечения, приоритеты."""

from datetime import date, time

from planner_bot.db.models import Priority, TimeWindow
from planner_bot.services.scheduling import (
    BusyBlock,
    Interval,
    SchedTask,
    build_day_plan,
    conflicts,
    free_slots,
)
from tests.conftest import NOW, TZ, local

DAY = date(2026, 11, 18)


def plan(busy, tasks, now=NOW, lunch=None):
    return build_day_plan(
        day=DAY, tz=TZ, now=now, day_start=time(8, 0), day_end=time(23, 0), busy=busy, tasks=tasks, lunch=lunch
    )


def test_free_slots_between_busy_intervals():
    window = Interval(local(2026, 11, 18, 8), local(2026, 11, 18, 20))
    busy = [
        Interval(local(2026, 11, 18, 9), local(2026, 11, 18, 12)),
        Interval(local(2026, 11, 18, 11), local(2026, 11, 18, 13)),  # перекрывается с предыдущим
        Interval(local(2026, 11, 18, 15), local(2026, 11, 18, 18)),
    ]
    slots = free_slots(busy, window)
    assert [(s.start.hour, s.end.hour) for s in slots] == [(8, 9), (13, 15), (18, 20)]


def test_tasks_never_overlap_fixed_events():
    busy = [
        BusyBlock(local(2026, 11, 18, 9), local(2026, 11, 18, 12), "Пара", event_id=1),
        BusyBlock(local(2026, 11, 18, 12), local(2026, 11, 18, 18), "Работа", event_id=2),
    ]
    tasks = [SchedTask(id=i, title=f"T{i}", estimated_minutes=90) for i in range(1, 5)]
    p = plan(busy, tasks)
    task_items = [i for i in p.items if i.kind == "task"]
    assert task_items, "задачи должны быть распланированы"
    for item in task_items:
        iv = Interval(item.start, item.end)
        assert not conflicts(iv, busy), f"{item.title} пересекается с событием"
    # Порядок в плане — по времени
    starts = [i.start for i in p.items]
    assert starts == sorted(starts)


def test_high_priority_and_deadline_first():
    tasks = [
        SchedTask(id=1, title="Low", priority=Priority.LOW, estimated_minutes=60),
        SchedTask(id=2, title="High", priority=Priority.HIGH, estimated_minutes=60),
        SchedTask(id=3, title="Due tonight", priority=Priority.MEDIUM, deadline=local(2026, 11, 18, 22)),
    ]
    p = plan([], tasks)
    order = [i.title for i in p.items if i.kind == "task"]
    assert order == ["Due tonight", "High", "Low"]
    assert p.main_task.title == "Due tonight"


def test_time_window_evening_respected():
    tasks = [SchedTask(id=1, title="Зал", time_window=TimeWindow.EVENING, estimated_minutes=60)]
    p = plan([], tasks)
    item = p.slot_for(1)
    assert item.start.astimezone(TZ).hour >= 18


def test_today_plan_starts_after_now():
    now = local(2026, 11, 18, 14, 2)
    p = plan([], [SchedTask(id=1, title="X", estimated_minutes=30)], now=now)
    assert p.slot_for(1).start == local(2026, 11, 18, 14, 5)


def test_task_that_does_not_fit_goes_to_unscheduled():
    busy = [BusyBlock(local(2026, 11, 18, 8), local(2026, 11, 18, 23), "Весь день", event_id=1)]
    p = plan(busy, [SchedTask(id=1, title="X", estimated_minutes=30)])
    assert p.slot_for(1) is None
    assert [t.id for t in p.unscheduled] == [1]


def test_lunch_added_only_when_free():
    p = plan([], [], lunch=(time(13), time(14)))
    assert any(i.kind == "break" for i in p.items)
    busy = [BusyBlock(local(2026, 11, 18, 12), local(2026, 11, 18, 18), "Работа", event_id=1)]
    p2 = plan(busy, [], lunch=(time(13), time(14)))
    assert not any(i.kind == "break" for i in p2.items)


def test_not_before_respected():
    t = SchedTask(id=1, title="X", estimated_minutes=60, not_before=local(2026, 11, 18, 18))
    p = plan([], [t])
    assert p.slot_for(1).start == local(2026, 11, 18, 18)
