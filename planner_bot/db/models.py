"""ORM-модели. Все моменты времени хранятся в UTC (timezone-aware)."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """DateTime, который всегда отдаёт aware-UTC (SQLite теряет tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed, use timezone-aware values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    DONE = "done"
    CANCELLED = "cancelled"


OPEN_STATUSES = (TaskStatus.PENDING, TaskStatus.SCHEDULED)


class Priority(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TimeWindow(enum.StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class ReminderKind(enum.StrEnum):
    BEFORE_START = "before_start"
    AFTER_END = "after_end"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    first_name: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64))
    morning_time: Mapped[time] = mapped_column(Time, default=time(8, 0))
    evening_time: Mapped[time] = mapped_column(Time, default=time(21, 30))
    day_start: Mapped[time] = mapped_column(Time, default=time(8, 0))
    day_end: Mapped[time] = mapped_column(Time, default=time(23, 0))
    reminder_minutes: Mapped[int] = mapped_column(Integer, default=15)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_morning_sent: Mapped[date | None] = mapped_column(Date)
    last_evening_sent: Mapped[date | None] = mapped_column(Date)
    # Ожидаемое действие (например, "pick_time:12") — переживает рестарт бота
    pending_action: Mapped[str | None] = mapped_column(String(64))
    # Последняя задача «в фокусе» — для фраз вроде «перенеси на завтра»
    context_task_id: Mapped[int | None] = mapped_column(Integer)

    tasks: Mapped[list[Task]] = relationship(back_populates="user", cascade="all, delete-orphan")
    events: Mapped[list[Event]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING, index=True)
    priority: Mapped[str] = mapped_column(String(8), default=Priority.MEDIUM)
    # День, на который задача запланирована пользователем («завтра купить продукты»)
    due_date: Mapped[date | None] = mapped_column(Date)
    # Крайний срок («до пятницы», «до 22:00»)
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer)
    time_window: Mapped[str | None] = mapped_column(String(16))
    scheduled_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    scheduled_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # True — время выбрал пользователь, планировщик его не двигает
    is_time_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Не раньше этого момента (для «перенеси на завтра после 18»)
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    user: Mapped[User] = relationship(back_populates="tasks")


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    start_time: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime)
    description: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="events")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    fire_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProcessedMessage(Base):
    """Защита от повторной обработки одного и того же сообщения Telegram."""

    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("chat_id", "message_id", name="uq_processed_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
