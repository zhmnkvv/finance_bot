"""Долги: «я должен» / «мне должны», частичные и полные погашения, напоминания о сроке."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Debt, DebtPayment, DebtStatus, User
from finance_bot.schemas.finance import DebtCreate
from finance_bot.utils.dates import now_utc

log = logging.getLogger(__name__)


class DebtError(ValueError):
    pass


class DebtService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user: User, debt_id: int) -> Debt | None:
        return await self.session.scalar(select(Debt).where(Debt.id == debt_id, Debt.user_id == user.id))

    async def list(self, user: User, *, active: bool = True, direction: str | None = None) -> list[Debt]:
        q = select(Debt).where(
            Debt.user_id == user.id, Debt.status == (DebtStatus.ACTIVE if active else DebtStatus.CLOSED)
        )
        if direction:
            q = q.where(Debt.direction == direction)
        q = q.order_by(Debt.due_on.is_(None), Debt.due_on, Debt.id) if active else q.order_by(Debt.closed_at.desc())
        return list(await self.session.scalars(q))

    async def create(self, user: User, data: DebtCreate) -> tuple[Debt, bool]:
        if data.client_token:
            existing = await self.session.scalar(
                select(Debt).where(Debt.user_id == user.id, Debt.client_token == data.client_token)
            )
            if existing is not None:
                return existing, False
        if data.due_on is not None and data.due_on < data.opened_on:
            raise DebtError("Дата возврата не может быть раньше даты долга.")
        debt = Debt(
            user_id=user.id,
            direction=str(data.direction),
            person=data.person,
            amount=data.amount,
            currency=data.currency,
            opened_on=data.opened_on,
            due_on=data.due_on,
            comment=data.comment,
            status=DebtStatus.ACTIVE,
            client_token=data.client_token,
        )
        self.session.add(debt)
        await self.session.flush()
        await self.session.refresh(debt, ["payments"])
        log.info("debt created id=%s user=%s", debt.id, user.id)
        return debt, True

    async def pay(
        self,
        user: User,
        debt: Debt,
        amount: Decimal,
        *,
        comment: str | None = None,
        client_token: str | None = None,
        at: datetime | None = None,
    ) -> DebtPayment:
        """Погашение (частичное или полное). При нулевом остатке долг закрывается."""
        if debt.user_id != user.id:
            raise DebtError("Долг не найден.")
        if client_token:
            for p in debt.payments:
                if p.client_token == client_token:
                    return p
        if debt.status != DebtStatus.ACTIVE:
            raise DebtError("Этот долг уже закрыт.")
        if amount <= 0:
            raise DebtError("Сумма должна быть больше нуля.")
        if amount > debt.remaining:
            raise DebtError("Сумма больше остатка долга.")
        payment = DebtPayment(debt_id=debt.id, amount=amount, comment=comment, client_token=client_token,
                              paid_at=at or now_utc())  # fmt: skip
        debt.payments.append(payment)
        await self.session.flush()
        if debt.remaining == 0:
            debt.status = DebtStatus.CLOSED
            debt.closed_at = at or now_utc()
            await self.session.flush()
        return payment

    async def pay_full(self, user: User, debt: Debt, *, client_token: str | None = None) -> DebtPayment:
        return await self.pay(user, debt, debt.remaining, comment="Полное погашение", client_token=client_token)

    async def delete(self, user: User, debt: Debt) -> None:
        if debt.user_id != user.id:
            raise DebtError("Долг не найден.")
        await self.session.delete(debt)
        await self.session.flush()

    async def due_for_reminder(self, user: User, today: date) -> list[tuple[Debt, int]]:
        """Активные долги, о которых пора напомнить сегодня: за 3 дня, за 1 день, в день и раз в 3 дня после."""
        out = []
        for debt in await self.list(user, active=True):
            if debt.due_on is None or debt.last_reminded_on == today:
                continue
            days_left = (debt.due_on - today).days
            if days_left in (3, 1, 0) or (days_left < 0 and (-days_left) % 3 == 1):
                out.append((debt, days_left))
        return out
