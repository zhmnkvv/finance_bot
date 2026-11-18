"""Агрегаты по операциям: суммы за период, распределение по категориям, баланс.

Валюты не смешиваются: каждая сумма считается в одной валюте, конвертации нет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Budget, Category, Transaction, TxKind, User
from finance_bot.utils.dates import Period, get_tz, month_bounds, day_start_utc, period

ZERO = Decimal("0.00")


def _dec(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


@dataclass
class Totals:
    income: Decimal = ZERO
    expense: Decimal = ZERO

    @property
    def diff(self) -> Decimal:
        return self.income - self.expense


@dataclass
class CategoryShare:
    category: Category
    amount: Decimal
    share: float  # 0..1


@dataclass
class BalanceSummary:
    currency: str
    balance: Decimal
    month_income: Decimal
    month_expense: Decimal
    budget_total: Decimal | None
    budget_left: Decimal | None
    top_category: CategoryShare | None
    # Баланс в других валютах (без конвертации)
    other_balances: dict[str, Decimal] = field(default_factory=dict)


class StatsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def totals(self, user: User, currency: str, start: datetime | None = None, end: datetime | None = None) -> Totals:
        cond = [Transaction.user_id == user.id, Transaction.currency == currency]
        if start is not None:
            cond.append(Transaction.occurred_at >= start)
        if end is not None:
            cond.append(Transaction.occurred_at < end)
        rows = await self.session.execute(
            select(Transaction.kind, func.sum(Transaction.amount)).where(*cond).group_by(Transaction.kind)
        )
        t = Totals()
        for kind, total in rows:
            if kind == TxKind.INCOME:
                t.income = _dec(total)
            elif kind == TxKind.EXPENSE:
                t.expense = _dec(total)
        return t

    async def balances(self, user: User) -> dict[str, Decimal]:
        """Текущий баланс за всё время по каждой валюте: доходы − расходы."""
        rows = await self.session.execute(
            select(Transaction.currency, Transaction.kind, func.sum(Transaction.amount))
            .where(Transaction.user_id == user.id)
            .group_by(Transaction.currency, Transaction.kind)
        )
        res: dict[str, Decimal] = {}
        for currency, kind, total in rows:
            sign = 1 if kind == TxKind.INCOME else -1
            res[currency] = res.get(currency, ZERO) + sign * _dec(total)
        return res

    async def by_category(
        self, user: User, currency: str, start: datetime, end: datetime, kind: str = TxKind.EXPENSE
    ) -> list[CategoryShare]:
        rows = await self.session.execute(
            select(Category, func.sum(Transaction.amount).label("total"))
            .join(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.user_id == user.id,
                Transaction.currency == currency,
                Transaction.kind == str(kind),
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
            .group_by(Category.id)
        )
        items = [(cat, _dec(total)) for cat, total in rows]
        grand = sum((a for _, a in items), ZERO)
        shares = [CategoryShare(c, a, float(a / grand) if grand else 0.0) for c, a in items]
        return sorted(shares, key=lambda s: (-s.amount, s.category.sort_order))

    async def spent_in_category(self, user: User, category_id: int, currency: str, start, end) -> Decimal:
        total = await self.session.scalar(
            select(func.sum(Transaction.amount)).where(
                Transaction.user_id == user.id,
                Transaction.category_id == category_id,
                Transaction.currency == currency,
                Transaction.kind == TxKind.EXPENSE,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
        )
        return _dec(total)

    async def monthly_series(self, user: User, currency: str, now: datetime, months: int = 6):
        """[(подпись, Totals)] за последние N месяцев, от старых к новым."""
        tz = get_tz(user.settings.timezone)
        today = now.astimezone(tz).date()
        y, m = today.year, today.month
        out = []
        for _ in range(months):
            a, b = month_bounds(y, m)
            t = await self.totals(user, currency, day_start_utc(a, tz), day_start_utc(b, tz))
            out.append((f"{m:02d}.{str(y)[2:]}", t))
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        return list(reversed(out))

    async def balance_summary(self, user: User, now: datetime) -> BalanceSummary:
        currency = user.settings.currency
        tz = get_tz(user.settings.timezone)
        month: Period = period("month", now, tz)

        balances = await self.balances(user)
        month_totals = await self.totals(user, currency, month.start, month.end)
        cats = await self.by_category(user, currency, month.start, month.end)

        budgets = list(
            await self.session.scalars(select(Budget).where(Budget.user_id == user.id, Budget.currency == currency))
        )
        budget_total = budget_left = None
        if budgets:
            spent_by_cat = {s.category.id: s.amount for s in cats}
            budget_total = sum((b.amount for b in budgets), ZERO)
            budget_left = budget_total - sum((spent_by_cat.get(b.category_id, ZERO) for b in budgets), ZERO)

        return BalanceSummary(
            currency=currency,
            balance=balances.pop(currency, ZERO),
            month_income=month_totals.income,
            month_expense=month_totals.expense,
            budget_total=budget_total,
            budget_left=budget_left,
            top_category=cats[0] if cats else None,
            other_balances={c: v for c, v in balances.items() if v != 0},
        )
