"""Месячные бюджеты по категориям и уведомления о порогах 50/80/100%."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Budget, BudgetAlert, TxKind, User
from finance_bot.schemas.finance import BudgetInput
from finance_bot.services.categories import CategoryService
from finance_bot.services.stats import StatsService
from finance_bot.utils.dates import get_tz, period

THRESHOLDS = (50, 80, 100)


class BudgetError(ValueError):
    pass


@dataclass
class BudgetProgress:
    budget: Budget
    spent: Decimal

    @property
    def left(self) -> Decimal:
        return self.budget.amount - self.spent

    @property
    def percent(self) -> int:
        if not self.budget.amount:
            return 0
        return int(self.spent * 100 / self.budget.amount)


@dataclass
class BudgetAlertEvent:
    progress: BudgetProgress
    threshold: int


class BudgetService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.categories = CategoryService(session)
        self.stats = StatsService(session)

    async def get(self, user: User, budget_id: int) -> Budget | None:
        return await self.session.scalar(select(Budget).where(Budget.id == budget_id, Budget.user_id == user.id))

    async def list(self, user: User) -> list[Budget]:
        res = await self.session.scalars(select(Budget).where(Budget.user_id == user.id).order_by(Budget.id))
        return list(res)

    async def upsert(self, user: User, data: BudgetInput) -> tuple[Budget, bool]:
        """Создаёт бюджет или меняет лимит существующего (категория+валюта уникальны)."""
        category = await self.categories.get(user, data.category_id, kind=TxKind.EXPENSE)
        if category is None:
            raise BudgetError("Категория не найдена.")
        existing = await self.session.scalar(
            select(Budget).where(
                Budget.user_id == user.id, Budget.category_id == category.id, Budget.currency == data.currency
            )
        )
        if existing is not None:
            existing.amount = data.amount
            await self._reset_alerts(existing)
            await self.session.flush()
            return existing, False
        budget = Budget(user_id=user.id, category_id=category.id, amount=data.amount, currency=data.currency)
        try:
            async with self.session.begin_nested():
                self.session.add(budget)
                await self.session.flush()
        except IntegrityError as e:
            raise BudgetError("Такой бюджет уже есть.") from e
        await self.session.refresh(budget, ["category"])
        return budget, True

    async def update_amount(self, user: User, budget: Budget, amount: Decimal) -> Budget:
        if budget.user_id != user.id:
            raise BudgetError("Бюджет не найден.")
        BudgetInput(category_id=budget.category_id, amount=amount, currency=budget.currency)  # валидация
        budget.amount = amount
        await self._reset_alerts(budget)
        await self.session.flush()
        return budget

    async def delete(self, user: User, budget: Budget) -> None:
        if budget.user_id != user.id:
            raise BudgetError("Бюджет не найден.")
        await self.session.delete(budget)
        await self.session.flush()

    async def _reset_alerts(self, budget: Budget) -> None:
        """Лимит изменился — пороги за текущий месяц пересчитываются заново."""
        for alert in await self.session.scalars(select(BudgetAlert).where(BudgetAlert.budget_id == budget.id)):
            await self.session.delete(alert)

    async def progress(self, user: User, budget: Budget, now: datetime) -> BudgetProgress:
        month = period("month", now, get_tz(user.settings.timezone))
        spent = await self.stats.spent_in_category(user, budget.category_id, budget.currency, month.start, month.end)
        return BudgetProgress(budget, spent)

    async def all_progress(self, user: User, now: datetime) -> list[BudgetProgress]:
        return [await self.progress(user, b, now) for b in await self.list(user)]

    async def check_alerts(self, user: User, category_id: int, currency: str, now: datetime) -> BudgetAlertEvent | None:
        """После расхода: если пересечён новый порог — записываем и возвращаем самый высокий.

        Каждый порог срабатывает один раз в месяц, поэтому повторные расходы не спамят.
        """
        budget = await self.session.scalar(
            select(Budget).where(
                Budget.user_id == user.id, Budget.category_id == category_id, Budget.currency == currency
            )
        )
        if budget is None:
            return None
        prog = await self.progress(user, budget, now)
        month_key = now.astimezone(get_tz(user.settings.timezone)).strftime("%Y-%m")
        sent = set(
            await self.session.scalars(
                select(BudgetAlert.threshold).where(BudgetAlert.budget_id == budget.id, BudgetAlert.period == month_key)
            )
        )
        reached = [t for t in THRESHOLDS if prog.percent >= t and t not in sent]
        if not reached:
            return None
        for t in reached:
            self.session.add(BudgetAlert(budget_id=budget.id, period=month_key, threshold=t))
        await self.session.flush()
        return BudgetAlertEvent(prog, max(reached))
