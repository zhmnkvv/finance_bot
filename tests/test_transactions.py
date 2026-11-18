"""Операции, баланс, статистика, валюты, изоляция пользователей."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from finance_bot.schemas.finance import TransactionCreate, TransactionUpdate
from finance_bot.services.categories import CategoryService
from finance_bot.services.settings import SettingsService
from finance_bot.services.stats import StatsService
from finance_bot.services.transactions import HistoryFilter, TransactionError, TransactionService
from finance_bot.utils.dates import period
from tests.conftest import EXPENSE, INCOME, NOW, TZ, add_tx, local


async def test_add_income(session, user):
    tx = await add_tx(session, user, INCOME, "250000", "salary", comment="  Ноябрь  ")
    assert tx.id and tx.kind == "income"
    assert tx.amount == Decimal("250000.00") and isinstance(tx.amount, Decimal)
    assert tx.currency == "KZT"
    assert tx.category.name == "Зарплата"
    assert tx.comment == "Ноябрь"


async def test_add_expense(session, user):
    tx = await add_tx(session, user, EXPENSE, "3500.50", "food")
    assert tx.kind == "expense" and tx.amount == Decimal("3500.50")
    assert tx.category.code == "food"


async def test_negative_or_zero_amount_rejected(session, user):
    cat = await CategoryService(session).by_code(user, EXPENSE, "food")
    for bad in ("-100", "0"):
        with pytest.raises(ValidationError):
            TransactionCreate(kind=EXPENSE, amount=Decimal(bad), currency="KZT", category_id=cat.id, occurred_at=NOW)


async def test_float_amount_never_stored(session, user):
    cat = await CategoryService(session).by_code(user, EXPENSE, "food")
    tx, _ = await TransactionService(session).create(
        user, TransactionCreate(kind=EXPENSE, amount="0.1", currency="KZT", category_id=cat.id, occurred_at=NOW)
    )
    assert tx.amount == Decimal("0.10")


async def test_category_kind_must_match(session, user):
    salary = await CategoryService(session).by_code(user, INCOME, "salary")
    with pytest.raises(TransactionError):
        await TransactionService(session).create(
            user, TransactionCreate(kind=EXPENSE, amount=1, currency="KZT", category_id=salary.id, occurred_at=NOW)
        )


async def test_repeated_confirm_does_not_duplicate(session, user):
    t1 = await add_tx(session, user, EXPENSE, 500, "food", token="flow-abc")
    t2 = await add_tx(session, user, EXPENSE, 500, "food", token="flow-abc")
    assert t1.id == t2.id
    page = await TransactionService(session).history(user, HistoryFilter())
    assert page.total == 1


async def test_edit_transaction(session, user):
    svc = TransactionService(session)
    tx = await add_tx(session, user, EXPENSE, 1000, "food", comment="обед")
    transport = await CategoryService(session).by_code(user, EXPENSE, "transport")
    await svc.update(user, tx, TransactionUpdate(amount=Decimal("1500"), category_id=transport.id, comment="такси"))
    tx = await svc.get(user, tx.id)
    assert (tx.amount, tx.category.code, tx.comment) == (Decimal("1500.00"), "transport", "такси")
    await svc.update(user, tx, TransactionUpdate(clear_comment=True))
    assert tx.comment is None


async def test_edit_cannot_switch_to_income_category(session, user):
    tx = await add_tx(session, user, EXPENSE, 1000, "food")
    salary = await CategoryService(session).by_code(user, INCOME, "salary")
    with pytest.raises(TransactionError):
        await TransactionService(session).update(user, tx, TransactionUpdate(category_id=salary.id))


async def test_delete_transaction(session, user):
    svc = TransactionService(session)
    tx = await add_tx(session, user, EXPENSE, 1000, "food")
    await svc.delete(user, tx)
    assert await svc.get(user, tx.id) is None


async def test_balance_is_all_time_not_only_month(session, user):
    # Прошлый месяц
    await add_tx(session, user, INCOME, 200000, "salary", at=local(2026, 10, 5))
    await add_tx(session, user, EXPENSE, 50000, "housing", at=local(2026, 10, 6))
    # Текущий месяц
    await add_tx(session, user, INCOME, 100000, "freelance", at=local(2026, 11, 3))
    await add_tx(session, user, EXPENSE, 30000, "food", at=local(2026, 11, 10))
    await add_tx(session, user, EXPENSE, 12000, "transport", at=local(2026, 11, 12))

    s = await StatsService(session).balance_summary(user, NOW)
    assert s.balance == Decimal("208000.00")  # 300000 − 92000 за всё время
    assert s.month_income == Decimal("100000.00")
    assert s.month_expense == Decimal("42000.00")
    assert s.top_category.category.code == "food" and s.top_category.amount == Decimal("30000.00")
    assert s.budget_left is None  # бюджетов нет


async def test_stats_periods_and_categories(session, user):
    await add_tx(session, user, EXPENSE, 1000, "food", at=NOW - timedelta(hours=1))  # сегодня
    await add_tx(session, user, EXPENSE, 2000, "food", at=local(2026, 11, 16))  # понедельник этой недели
    await add_tx(session, user, EXPENSE, 4000, "fun", at=local(2026, 11, 2))  # этот месяц
    await add_tx(session, user, EXPENSE, 8000, "transport", at=local(2026, 3, 1))  # этот год
    await add_tx(session, user, INCOME, 50000, "salary", at=local(2026, 11, 1, 9))

    st = StatsService(session)

    async def exp(kind):
        p = period(kind, NOW, TZ)
        return (await st.totals(user, "KZT", p.start, p.end)).expense

    assert await exp("day") == Decimal("1000.00")
    assert await exp("week") == Decimal("3000.00")
    assert await exp("month") == Decimal("7000.00")
    assert await exp("year") == Decimal("15000.00")

    month = period("month", NOW, TZ)
    totals = await st.totals(user, "KZT", month.start, month.end)
    assert totals.diff == Decimal("43000.00")
    shares = await st.by_category(user, "KZT", month.start, month.end)
    assert [s.category.code for s in shares] == ["fun", "food"]
    assert round(shares[0].share, 3) == round(4000 / 7000, 3)


async def test_month_comparison(session, user):
    await add_tx(session, user, EXPENSE, 10000, "food", at=local(2026, 10, 15))
    await add_tx(session, user, EXPENSE, 15000, "food", at=local(2026, 11, 15))
    st = StatsService(session)
    cur, prev = period("month", NOW, TZ), period("prev_month", NOW, TZ)
    assert (await st.totals(user, "KZT", cur.start, cur.end)).expense == Decimal("15000.00")
    assert (await st.totals(user, "KZT", prev.start, prev.end)).expense == Decimal("10000.00")
    series = await st.monthly_series(user, "KZT", NOW, months=3)
    assert [label for label, _ in series] == ["09.26", "10.26", "11.26"]


async def test_currencies_are_not_mixed(session, user):
    await add_tx(session, user, INCOME, 100000, "salary")
    await add_tx(session, user, INCOME, 500, "freelance", currency="USD")
    await add_tx(session, user, EXPENSE, 100, "shopping", currency="USD")

    st = StatsService(session)
    s = await st.balance_summary(user, NOW)
    assert s.currency == "KZT" and s.balance == Decimal("100000.00")
    assert s.other_balances == {"USD": Decimal("400.00")}

    # Смена основной валюты не конвертирует старые операции
    await SettingsService(session).set_currency(user, "USD")
    s = await st.balance_summary(user, NOW)
    assert s.currency == "USD" and s.balance == Decimal("400.00")
    assert s.other_balances == {"KZT": Decimal("100000.00")}


async def test_history_filters_and_pagination(session, user):
    for i in range(12):
        await add_tx(session, user, EXPENSE, 100 + i, "food", at=NOW - timedelta(days=i))
    await add_tx(session, user, INCOME, 5000, "gifts", at=NOW)
    svc = TransactionService(session)

    p0 = await svc.history(user, HistoryFilter(), page=0, page_size=5)
    assert (p0.total, p0.pages, len(p0.items)) == (13, 3, 5)
    p2 = await svc.history(user, HistoryFilter(), page=2, page_size=5)
    assert len(p2.items) == 3
    assert (await svc.history(user, HistoryFilter(), page=99, page_size=5)).page == 2

    assert (await svc.history(user, HistoryFilter(kind="income"))).total == 1
    food = await CategoryService(session).by_code(user, EXPENSE, "food")
    assert (await svc.history(user, HistoryFilter(category_id=food.id))).total == 12
    week = period("week", NOW, TZ)
    assert (await svc.history(user, HistoryFilter(start=week.start, end=week.end))).total == 4


async def test_users_are_isolated(session, user, other_user):
    tx = await add_tx(session, user, EXPENSE, 1000, "food")
    svc = TransactionService(session)
    assert await svc.get(other_user, tx.id) is None
    with pytest.raises(TransactionError):
        await svc.update(other_user, tx, TransactionUpdate(amount=Decimal("1")))
    with pytest.raises(TransactionError):
        await svc.delete(other_user, tx)
    assert (await svc.history(other_user, HistoryFilter())).total == 0
    s = await StatsService(session).balance_summary(other_user, NOW)
    assert s.balance == 0 and s.month_expense == 0
    # Один и тот же client_token у разных пользователей — это разные операции
    a = await add_tx(session, user, EXPENSE, 1, "food", token="same")
    b = await add_tx(session, other_user, EXPENSE, 1, "food", token="same")
    assert a.id != b.id
