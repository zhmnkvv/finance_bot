"""Строгая проверка ответа LLM и приведение к доменным типам.

Каждый элемент проверяется отдельно: одна кривая задача не ломает весь ответ,
а попадает в warnings.
"""

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from planner_bot.schemas.ai import AIParseResult
from planner_bot.schemas.commands import EventDraft, ParsedCommand, RescheduleSpec, TaskDraft
from planner_bot.utils.dates import parse_hhmm, parse_iso_date, parse_local_iso

log = logging.getLogger(__name__)

DEFAULT_EVENT_MINUTES = 60
# Разумные границы: всё, что дальше года вперёд или раньше вчера, скорее всего ошибка модели
MAX_AHEAD = timedelta(days=366)
MAX_BEHIND = timedelta(days=1)


def _deadline(value: str, tz: ZoneInfo) -> datetime:
    value = value.strip()
    if len(value) == 10:  # только дата → конец дня
        return datetime.combine(parse_iso_date(value), time(23, 59), tzinfo=tz)
    return parse_local_iso(value, tz)


def _in_range(dt: datetime, now: datetime) -> bool:
    return now - MAX_BEHIND <= dt <= now + MAX_AHEAD


def _opt_time(value: str | None) -> time | None:
    return parse_hhmm(value) if value else None


def to_command(raw: AIParseResult, tz: ZoneInfo, now: datetime) -> ParsedCommand:
    warnings: list[str] = []
    tasks: list[TaskDraft] = []
    events: list[EventDraft] = []

    for t in raw.tasks:
        try:
            deadline = _deadline(t.deadline, tz) if t.deadline else None
            start = parse_local_iso(t.start, tz) if t.start else None
            due = parse_iso_date(t.due_date) if t.due_date else None
            for dt in (deadline, start):
                if dt is not None and not _in_range(dt, now):
                    raise ValueError(f"date out of range: {dt}")
            minutes = t.estimated_minutes
            if minutes is not None and not 5 <= minutes <= 720:
                minutes = None
            tasks.append(
                TaskDraft(
                    title=t.title,
                    due_date=due,
                    deadline=deadline,
                    priority=t.priority or "medium",
                    estimated_minutes=minutes,
                    time_window=t.time_window,
                    start=start,
                )
            )
        except (ValueError, ValidationError) as e:
            log.warning("AI task rejected: %r (%s)", t.title, e)
            warnings.append(f"Не смог разобрать задачу «{t.title}»")

    for e in raw.events:
        try:
            start = parse_local_iso(e.start, tz)
            end = parse_local_iso(e.end, tz) if e.end else start + timedelta(minutes=DEFAULT_EVENT_MINUTES)
            if not _in_range(start, now):
                raise ValueError(f"date out of range: {start}")
            events.append(EventDraft(title=e.title, start=start, end=end, description=e.description))
        except (ValueError, ValidationError) as err:
            log.warning("AI event rejected: %r (%s)", e.title, err)
            warnings.append(f"Не смог разобрать событие «{e.title}»")

    reschedule = None
    if raw.reschedule is not None:
        try:
            reschedule = RescheduleSpec(
                day=parse_iso_date(raw.reschedule.date) if raw.reschedule.date else None,
                after_time=_opt_time(raw.reschedule.after_time),
                exact_time=_opt_time(raw.reschedule.exact_time),
            )
        except ValueError as err:
            log.warning("AI reschedule rejected: %s", err)
            warnings.append("Не понял, на когда перенести")

    plan_date = None
    if raw.plan_date:
        try:
            plan_date = parse_iso_date(raw.plan_date)
        except ValueError:
            plan_date = None

    return ParsedCommand(
        intent=raw.intent,
        tasks=tasks,
        events=events,
        target_title=(raw.target_title or "").strip() or None,
        reschedule=reschedule,
        plan_date=plan_date,
        clarification_question=raw.clarification_question,
        warnings=warnings,
    )
