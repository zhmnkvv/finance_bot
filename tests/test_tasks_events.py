"""Создание задач/событий, дедупликация, поиск по названию."""

from datetime import date

import pytest
from pydantic import ValidationError

from planner_bot.db.models import Priority, TaskStatus
from planner_bot.schemas.commands import EventDraft, TaskDraft
from planner_bot.services.events import EventService
from planner_bot.services.tasks import TaskService
from tests.conftest import local


async def test_create_task(session, user):
    svc = TaskService(session)
    task, created = await svc.create(
        user,
        TaskDraft(
            title="закончить лабораторную по NLP",
            deadline=local(2026, 11, 20, 23, 59),
            priority=Priority.HIGH,
            estimated_minutes=120,
        ),
    )
    assert created
    assert task.id is not None
    assert task.title == "Закончить лабораторную по NLP"
    assert task.status == TaskStatus.PENDING
    assert task.deadline == local(2026, 11, 20, 23, 59)
    assert task.deadline.tzinfo is not None


async def test_duplicate_task_is_not_created(session, user):
    svc = TaskService(session)
    draft = TaskDraft(title="Купить продукты", due_date=date(2026, 11, 19))
    t1, c1 = await svc.create(user, draft)
    t2, c2 = await svc.create(user, TaskDraft(title="купить  продукты", due_date=date(2026, 11, 19)))
    assert c1 and not c2
    assert t1.id == t2.id
    assert len(await svc.list_open(user)) == 1


async def test_task_with_exact_start_is_fixed(session, user):
    svc = TaskService(session)
    task, _ = await svc.create(
        user, TaskDraft(title="Позвонить маме", start=local(2026, 11, 18, 19), estimated_minutes=30)
    )
    assert task.is_time_fixed
    assert task.scheduled_end == local(2026, 11, 18, 19, 30)
    assert task.due_date == date(2026, 11, 18)


async def test_find_by_title_fuzzy(session, user):
    svc = TaskService(session)
    await svc.create(user, TaskDraft(title="Лабораторная по NLP"))
    await svc.create(user, TaskDraft(title="Купить продукты"))
    found = await svc.find_by_title(user, "лабораторную")
    assert [t.title for t in found] == ["Лабораторная по NLP"]
    found = await svc.find_by_title(user, "продукты")
    assert [t.title for t in found] == ["Купить продукты"]


async def test_create_event_and_dedupe(session, user):
    svc = EventService(session)
    draft = EventDraft(title="пара", start=local(2026, 11, 19, 10), end=local(2026, 11, 19, 12))
    ev, created, overlaps = await svc.create(user, draft)
    assert created and overlaps == []
    assert ev.title == "Пара"
    assert ev.start_time == local(2026, 11, 19, 10)
    ev2, created2, _ = await svc.create(user, draft)
    assert not created2 and ev2.id == ev.id
    assert len(await svc.list_for_day(user, date(2026, 11, 19))) == 1


async def test_overlapping_event_reported(session, user):
    svc = EventService(session)
    await svc.create(user, EventDraft(title="Пара", start=local(2026, 11, 19, 10), end=local(2026, 11, 19, 12)))
    _, created, overlaps = await svc.create(
        user, EventDraft(title="Встреча", start=local(2026, 11, 19, 11), end=local(2026, 11, 19, 13))
    )
    assert created
    assert [e.title for e in overlaps] == ["Пара"]


def test_event_end_before_start_rejected():
    with pytest.raises(ValidationError):
        EventDraft(title="Пара", start=local(2026, 11, 19, 12), end=local(2026, 11, 19, 10))


def test_naive_datetime_rejected():
    from datetime import datetime

    with pytest.raises(ValidationError):
        TaskDraft(title="X", deadline=datetime(2026, 11, 19, 10))
