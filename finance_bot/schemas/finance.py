"""Pydantic-схемы входных данных сервисов. Всё, что приходит от пользователя, проходит через них."""

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from finance_bot.config import SUPPORTED_CURRENCIES
from finance_bot.db.models import DebtDirection, ReminderRepeat, TxKind
from finance_bot.utils.money import MAX_AMOUNT

PositiveMoney = Field(gt=Decimal("0"), le=MAX_AMOUNT, max_digits=14, decimal_places=2)


def _currency(v: str) -> str:
    v = v.upper()
    if v not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported currency {v}")
    return v


def _clean_text(v: str | None, max_len: int) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    return v[:max_len] or None


class TransactionCreate(BaseModel):
    kind: TxKind
    amount: Decimal = PositiveMoney
    currency: str
    category_id: int
    comment: str | None = None
    occurred_at: datetime
    client_token: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        return _currency(v)

    @field_validator("comment")
    @classmethod
    def _comment(cls, v):
        return _clean_text(v, 255)

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, v: datetime):
        if v.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return v


class TransactionUpdate(BaseModel):
    amount: Decimal | None = Field(default=None, gt=Decimal("0"), le=MAX_AMOUNT, max_digits=14, decimal_places=2)
    category_id: int | None = None
    comment: str | None = None
    clear_comment: bool = False
    occurred_at: datetime | None = None

    @field_validator("comment")
    @classmethod
    def _comment(cls, v):
        return _clean_text(v, 255)


class BudgetInput(BaseModel):
    category_id: int
    amount: Decimal = PositiveMoney
    currency: str

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        return _currency(v)


class DebtCreate(BaseModel):
    direction: DebtDirection
    person: str = Field(min_length=1, max_length=128)
    amount: Decimal = PositiveMoney
    currency: str
    opened_on: date
    due_on: date | None = None
    comment: str | None = None
    client_token: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        return _currency(v)

    @field_validator("person")
    @classmethod
    def _person(cls, v):
        v = _clean_text(v, 128)
        if not v:
            raise ValueError("empty person")
        return v

    @field_validator("comment")
    @classmethod
    def _comment(cls, v):
        return _clean_text(v, 255)


class ReminderCreate(BaseModel):
    kind: str = Field(pattern=r"^(internet|rent|subscription|debt|salary|other)$")
    title: str = Field(min_length=1, max_length=128)
    amount: Decimal | None = Field(default=None, gt=Decimal("0"), le=MAX_AMOUNT, max_digits=14, decimal_places=2)
    currency: str
    first_date: date
    local_time: time
    repeat: ReminderRepeat = ReminderRepeat.NONE
    debt_id: int | None = None
    client_token: str | None = Field(default=None, max_length=64)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v):
        return _currency(v)

    @field_validator("title")
    @classmethod
    def _title(cls, v):
        v = _clean_text(v, 128)
        if not v:
            raise ValueError("empty title")
        return v
