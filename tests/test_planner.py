"""План дня из БД, перенос задач, напоминания."""

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from planner_bot.db.models import Reminder, ReminderKind, TaskStatus
from planner_bot.schemas.commands import EventDraft, RescheduleSpec, TaskDraft
from planner_bot.services.events import EventService
from planner_bot.services.planner import PlannerService, RescheduleError
from planner_bot.services.scheduling import Interval
from planner_bot.services.tasks import TaskService
from tests.conftest import NOW, local

TODAY = date(2026, 11, 18)
TOMORROW = date(2026, 11, 19)


async def _seed_day(session, user):
    ev = EventService(session)
    await ev.create(user, EventDraft(title="Пара", start=local(2026, 11, 18, 9), end=local(2026, 11, 18, 12)))
    await ev.create(user, EventDraft(title="Работа", start=local(2026, 11, 18, 14), end=local(2026, 11, 18, 18)))
    ts = TaskService(session)
    lab, _ = await ts.create(user, TaskDraft(title="Лабораторная", estimated_minutes=120, priority="high"))
    gym, _ = await ts.create(user, TaskDraft(title="Зал", estimated_minutes=60, time_window="evening"))
    return lab, gym


async def test_plan_today_respects_events_and_persists(session, user):
    lab, gym = await _seed_day(session, user)
    plan = await PlannerService(session).plan_day(user, TODAY, now=NOW)

    events = [Interval(i.start, i.end) for i in plan.items if i.kind == "event"]
    tasks = [i for i in plan.items if i.kind == "task"]
    assert len(events) == 2 and len(tasks) == 2
    for t in tasks:
        assert not any(Interval(t.start, t.end).overlaps(e) for e in events)

    # Приоритетная лабораторная — в первое подходящее окно после обеда: 12:00–13:00 мало, обед 13–14,
    # затем работа до 18 → 18:00–20:00
    assert plan.slot_for(lab.id).start == local(2026, 11, 18, 18)
    assert plan.slot_for(gym.id).start == local(2026, 11, 18, 20)

    # Расписание и напоминания сохранены
    assert lab.status == TaskStatus.SCHEDULED
    assert lab.scheduled_start == local(2026, 11, 18, 18)
    kinds = set(
        await session.scalars(select(Reminder.kind).where(Reminder.task_id == lab.id))
    )
    assert kinds == {ReminderKind.BEFORE_START, ReminderKind.AFTER_END}
    before = await session.scalar(
        select(Reminder).where(Reminder.task_id == lab.id, Reminder.kind == ReminderKind.BEFORE_START)
    )
    assert before.fire_at == local(2026, 11, 18, 17, 45)


async def test_replanning_does_not_duplicate_reminders(session, user):
    await _seed_day(session, user)
    planner = PlannerService(session)
    await planner.plan_day(user, TODAY, now=NOW)
    await planner.plan_day(user, TODAY, now=NOW)
    reminders = list(await session.scalars(select(Reminder).where(Reminder.task_id.is_not(None))))
    assert len(reminders) == 4  # 2 задачи × (до начала + после конца)


async def test_tomorrow_task_not_in_today_plan(session, user):
    ts = TaskService(session)
    t, _ = await ts.create(user, TaskDraft(title="Купить продукты", due_date=TOMORROW))
    planner = PlannerService(session)
    today = await planner.plan_day(user, TODAY, now=NOW)
    assert today.slot_for(t.id) is None and t.id not in [u.id for u in today.unscheduled]
    tomorrow = await planner.plan_day(user, TOMORROW, now=NOW)
    assert tomorrow.slot_for(t.id) is not None
    assert t.scheduled_start is None  # предпросмотр завтрашнего дня ничего не сохраняет


async def test_reschedule_tomorrow_after_18(session, user):
    ts = TaskService(session)
    t, _ = await ts.create(user, TaskDraft(title="Лабораторная", estimated_minutes=60))
    planner = PlannerService(session)
    await planner.plan_day(user, TODAY, now=NOW)
    assert t.scheduled_start is not None

    res = await planner.reschedule(user, t, RescheduleSpec(day=TOMORROW, after_time=time(18)), now=NOW)
    assert res.day == TOMORROW
    assert res.start == local(2026, 11, 19, 18)
    assert t.due_date == TOMORROW
    assert t.scheduled_start is None  # на будущий день время назначит утренний план
    # Неотправленные напоминания на сегодня удалены
    left = list(await session.scalars(select(Reminder).where(Reminder.task_id == t.id)))
    assert left == []


async def test_reschedule_exact_time_shifts_past_event(session, user):
    await EventService(session).create(
        user, EventDraft(title="Пара", start=local(2026, 11, 18, 15), end=local(2026, 11, 18, 17))
    )
    t, _ = await TaskService(session).create(user, TaskDraft(title="Отчёт", estimated_minutes=60))
    res = await PlannerService(session).reschedule(
        user, t, RescheduleSpec(day=TODAY, exact_time=time(16)), now=NOW
    )
    assert res.adjusted
    assert res.start == local(2026, 11, 18, 17)
    assert t.is_time_fixed


async def test_reschedule_into_past_rejected(session, user):
    t, _ = await TaskService(session).create(user, TaskDraft(title="Отчёт"))
    with pytest.raises(RescheduleError):
        await PlannerService(session).reschedule(user, t, RescheduleSpec(day=TODAY, exact_time=time(7)), now=NOW)


async def test_move_open_to_tomorrow(session, user):
    ts = TaskService(session)
    a, _ = await ts.create(user, TaskDraft(title="A", due_date=TODAY))
    b, _ = await ts.create(user, TaskDraft(title="B", due_date=TOMORROW + timedelta(days=3)))
    moved = await PlannerService(session).move_open_to_tomorrow(user, now=NOW)
    assert [m.id for m in moved] == [a.id]
    assert a.due_date == TOMORROW and b.due_date == TOMORROW + timedelta(days=3)
