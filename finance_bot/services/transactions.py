"""Операции: создание (идемпотентное), редактирование, удаление, история с фильтрами."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Transaction, User
from finance_bot.schemas.finance import TransactionCreate, TransactionUpdate
from finance_bot.services.categories import CategoryService

log = logging.getLogger(__name__)


class TransactionError(ValueError):
    """Ошибка с понятным пользователю текстом."""


@dataclass
class HistoryFilter:
    kind: str | None = None  # income | expense | None
    category_id: int | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass
class Page:
    items: list[Transaction]
    page: int
    pages: int
    total: int


class TransactionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.categories = CategoryService(session)

    async def get(self, user: User, tx_id: int) -> Transaction | None:
        """Только операции этого пользователя — чужие недоступны даже по прямому id."""
        return await self.session.scalar(
            select(Transaction).where(Transaction.id == tx_id, Transaction.user_id == user.id)
        )

    async def create(self, user: User, data: TransactionCreate) -> tuple[Transaction, bool]:
        """Возвращает (операция, создана_ли). Повтор с тем же client_token вернёт уже сохранённую."""
        if data.client_token:
            existing = await self.session.scalar(
                select(Transaction).where(
                    Transaction.user_id == user.id, Transaction.client_token == data.client_token
                )
            )
            if existing is not None:
                return existing, False
        category = await self.categories.get(user, data.category_id, kind=data.kind)
        if category is None:
            raise TransactionError("Категория не найдена.")
        tx = Transaction(
            user_id=user.id,
            kind=str(data.kind),
            amount=data.amount,
            currency=data.currency,
            category_id=category.id,
            comment=data.comment,
            occurred_at=data.occurred_at,
            client_token=data.client_token,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(tx)
                await self.session.flush()
        except IntegrityError:
            # Гонка двух одновременных нажатий: второе получит уже созданную запись
            existing = await self.session.scalar(
                select(Transaction).where(
                    Transaction.user_id == user.id, Transaction.client_token == data.client_token
                )
            )
            if existing is None:
                raise
            return existing, False
        await self.session.refresh(tx, ["category"])
        log.info("transaction created id=%s user=%s kind=%s", tx.id, user.id, tx.kind)
        return tx, True

    async def update(self, user: User, tx: Transaction, data: TransactionUpdate) -> Transaction:
        if tx.user_id != user.id:
            raise TransactionError("Операция не найдена.")
        if data.amount is not None:
            tx.amount = data.amount
        if data.category_id is not None:
            category = await self.categories.get(user, data.category_id, kind=tx.kind)
            if category is None:
                raise TransactionError("Категория не найдена.")
            tx.category_id = category.id
        if data.clear_comment:
            tx.comment = None
        elif data.comment is not None:
            tx.comment = data.comment
        if data.occurred_at is not None:
            tx.occurred_at = data.occurred_at
        await self.session.flush()
        await self.session.refresh(tx, ["category"])
        return tx

    async def delete(self, user: User, tx: Transaction) -> None:
        if tx.user_id != user.id:
            raise TransactionError("Операция не найдена.")
        await self.session.delete(tx)
        await self.session.flush()

    async def history(self, user: User, flt: HistoryFilter, page: int = 0, page_size: int = 8) -> Page:
        cond = [Transaction.user_id == user.id]
        if flt.kind:
            cond.append(Transaction.kind == flt.kind)
        if flt.category_id:
            cond.append(Transaction.category_id == flt.category_id)
        if flt.start:
            cond.append(Transaction.occurred_at >= flt.start)
        if flt.end:
            cond.append(Transaction.occurred_at < flt.end)
        total = await self.session.scalar(select(func.count(Transaction.id)).where(*cond)) or 0
        pages = max(1, -(-total // page_size))
        page = min(max(page, 0), pages - 1)
        items = list(
            await self.session.scalars(
                select(Transaction)
                .where(*cond)
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
                .offset(page * page_size)
                .limit(page_size)
            )
        )
        return Page(items=items, page=page, pages=pages, total=total)
