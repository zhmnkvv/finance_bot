"""Провалидированные доменные команды — то, с чем работает бизнес-логика."""

from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, Field, field_validator, model_validator

from planner_bot.db.models import Priority, TimeWindow
from planner_bot.schemas.ai import Intent

MAX_EVENT_DURATION = timedelta(hours=24)


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return v


class TaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    due_date: date | None = None
    deadline: datetime | None = None
    priority: Priority = Priority.MEDIUM
    estimated_minutes: int | None = Field(default=None, ge=5, le=720)
    time_window: TimeWindow | None = None
    start: datetime | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = " ".join(v.split()).strip(" .,!")
        if not v:
            raise ValueError("empty title")
        return v[0].upper() + v[1:]

    @field_validator("deadline", "start")
    @classmethod
    def _check_aware(cls, v):
        return _aware(v)


class EventDraft(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start: datetime
    end: datetime
    description: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = " ".join(v.split()).strip(" .,!")
        if not v:
            raise ValueError("empty title")
        return v[0].upper() + v[1:]

    @field_validator("start", "end")
    @classmethod
    def _check_aware(cls, v):
        return _aware(v)

    @model_validator(mode="after")
    def _check_range(self):
        if self.end <= self.start:
            raise ValueError("event end must be after start")
        if self.end - self.start > MAX_EVENT_DURATION:
            raise ValueError("event is longer than 24 hours")
        return self


class RescheduleSpec(BaseModel):
    date: date | None = None
    after_time: time | None = None
    exact_time: time | None = None


class ParsedCommand(BaseModel):
    intent: Intent
    tasks: list[TaskDraft] = []
    events: list[EventDraft] = []
    target_title: str | None = None
    reschedule: RescheduleSpec | None = None
    plan_date: date | None = None
    clarification_question: str | None = None
    # Что пришлось отбросить при валидации — показываем пользователю мягко
    warnings: list[str] = []
