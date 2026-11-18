"""ORM-модели Finance Tracker.

- Деньги — Numeric(14, 2) ↔ Decimal, никогда не float.
- Моменты времени — UTC (timezone-aware), показываются в поясе пользователя.
- Все пользовательские данные привязаны к user_id.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(14, 2)


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


class Money(TypeDecorator):
    """Numeric(14,2), который всегда возвращает Decimal (SQLite иначе может отдать float)."""

    impl = MONEY
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float is not allowed for money, use Decimal")
        return Decimal(value).quantize(Decimal("0.01"))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value)).quantize(Decimal("0.01"))


class TxKind(enum.StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class DebtDirection(enum.StrEnum):
    I_OWE = "i_owe"  # я должен
    OWED_TO_ME = "owed_to_me"  # мне должны


class DebtStatus(enum.StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class ReminderRepeat(enum.StrEnum):
    NONE = "none"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    settings: Mapped[UserSettings] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="joined"
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Qyzylorda")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Во сколько присылать напоминания о сроках долгов (локальное время)
    debt_reminder_time: Mapped[time] = mapped_column(Time, default=time(10, 0))

    user: Mapped[User] = relationship(back_populates="settings")


class Category(Base):
    """Категория операции. user_id = NULL — системная (общая для всех), иначе личная."""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "kind", "code", name="uq_category_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    emoji: Mapped[str] = mapped_column(String(8), default="•")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("user_id", "client_token", name="uq_transaction_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    comment: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    # Идемпотентность: повторное нажатие «Сохранить» не создаёт дубль
    client_token: Mapped[str | None] = mapped_column(String(64))

    category: Mapped[Category] = relationship(lazy="joined")


class Budget(TimestampMixin, Base):
    """Месячный лимит расходов по категории в конкретной валюте."""

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "category_id", "currency", name="uq_budget"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))

    category: Mapped[Category] = relationship(lazy="joined")


class BudgetAlert(Base):
    """Какие пороги (50/80/100%) уже отправлены в каком месяце — чтобы не спамить."""

    __tablename__ = "budget_alerts"
    __table_args__ = (UniqueConstraint("budget_id", "period", "threshold", name="uq_budget_alert"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    threshold: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Debt(TimestampMixin, Base):
    __tablename__ = "debts"
    __table_args__ = (UniqueConstraint("user_id", "client_token", name="uq_debt_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(12))
    person: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    opened_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(8), default=DebtStatus.ACTIVE, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
    client_token: Mapped[str | None] = mapped_column(String(64))

    payments: Mapped[list[DebtPayment]] = relationship(
        back_populates="debt", cascade="all, delete-orphan", order_by="DebtPayment.paid_at", lazy="selectin"
    )

    @property
    def paid(self) -> Decimal:
        return sum((p.amount for p in self.payments), Decimal("0.00"))

    @property
    def remaining(self) -> Decimal:
        return max(self.amount - self.paid, Decimal("0.00"))


class DebtPayment(Base):
    __tablename__ = "debt_payments"
    __table_args__ = (UniqueConstraint("debt_id", "client_token", name="uq_debt_payment_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    debt_id: Mapped[int] = mapped_column(ForeignKey("debts.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    paid_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    comment: Mapped[str | None] = mapped_column(String(255))
    client_token: Mapped[str | None] = mapped_column(String(64))

    debt: Mapped[Debt] = relationship(back_populates="payments")


class Reminder(TimestampMixin, Base):
    """Напоминание о платеже. next_run_at — следующий момент отправки (UTC)."""

    __tablename__ = "reminders"
    __table_args__ = (UniqueConstraint("user_id", "client_token", name="uq_reminder_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # internet|rent|subscription|debt|salary|other
    title: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    repeat: Mapped[str] = mapped_column(String(8), default=ReminderRepeat.NONE)
    # Локальные «якоря» для повторов: день месяца/недели и время
    local_time: Mapped[time] = mapped_column(Time)
    anchor_day: Mapped[int] = mapped_column(Integer)  # день месяца (1–31) или день недели (0–6)
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    debt_id: Mapped[int | None] = mapped_column(ForeignKey("debts.id", ondelete="SET NULL"))
    client_token: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
