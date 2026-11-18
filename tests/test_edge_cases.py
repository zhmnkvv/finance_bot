"""Финальная проверка финансовой логики на граничных случаях."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from finance_bot.db.models import DebtDirection
from finance_bot.schemas.finance import BudgetInput, DebtCreate, TransactionUpdate
from finance_bot.services.budgets import BudgetService
from finance_bot.services.categories import CategoryService
from finance_bot.services.debts import DebtService
from finance_bot.services.settings import SettingsService
from finance_bot.services.stats import StatsService
from finance_bot.services.transactions import HistoryFilter, TransactionService
from finance_bot.utils.dates import get_tz, period
from tests.conftest import EXPENSE, INCOME, NOW, TZ, add_tx, local

# ------------------------------------------------------------------ валюты


async def test_four_currencies_never_mix(session, user):
    await add_tx(session, user, INCOME, 500000, "salary", currency="KZT")
    await add_tx(session, user, EXPENSE, 120000, "housing", currency="KZT")
    await add_tx(session, user, INCOME, 1000, "freelance", currency="USD")
    await add_tx(session, user, EXPENSE, 250.50, "shopping", currency="USD")
    await add_tx(session, user, INCOME, 300, "gifts", currency="EUR")
    await add_tx(session, user, EXPENSE, 5000, "fun", currency="RUB")

    st = StatsService(session)
    assert await st.balances(user) == {
        "KZT": Decimal("380000.00"),
        "USD": Decimal("749.50"),
        "EUR": Decimal("300.00"),
        "RUB": Decimal("-5000.00"),
    }
    month = period("month", NOW, TZ)
    for cur, inc, exp in [("KZT", "500000", "120000"), ("USD", "1000", "250.50"), ("EUR", "300", "0"),
                          ("RUB", "0", "5000")]:  # fmt: skip
        t = await st.totals(user, cur, month.start, month.end)
        assert (t.income, t.expense) == (Decimal(inc), Decimal(exp)), cur
    # Категории считаются только в выбранной валюте
    shares = await st.by_category(user, "USD", month.start, month.end)
    assert [(s.category.code, s.amount) for s in shares] == [("shopping", Decimal("250.50"))]

    s = await st.balance_summary(user, NOW)
    assert s.currency == "KZT" and s.balance == Decimal("380000.00")
    assert s.month_expense == Decimal("120000.00")  # без USD/RUB
    assert s.top_category.category.code == "housing"
    assert s.other_balances == {"USD": Decimal("749.50"), "EUR": Decimal("300.00"), "RUB": Decimal("-5000.00")}

    for cur in ("USD", "EUR", "RUB"):
        await SettingsService(session).set_currency(user, cur)
        s = await st.balance_summary(user, NOW)
        assert s.balance == (await st.balances(user))[cur] and "KZT" in s.other_balances


async def test_decimal_precision_on_many_small_amounts(session, user):
    for _ in range(10):
        await add_tx(session, user, EXPENSE, "0.10", "food")
    month = period("month", NOW, TZ)
    assert (await StatsService(session).totals(user, "KZT", month.start, month.end)).expense == Decimal("1.00")


# ------------------------------------------------------------------ периоды и часовые пояса


async def test_local_midnight_boundary_not_utc(session, user):
    # 01.12 00:30 по Кызылорде (UTC+5) — это 30.11 19:30 UTC, но операция декабрьская
    dec_tx = await add_tx(session, user, EXPENSE, 700, "food", at=local(2026, 12, 1, 0, 30))
    assert dec_tx.occurred_at.astimezone(UTC).day == 30
    nov_tx = await add_tx(session, user, EXPENSE, 300, "food", at=local(2026, 11, 30, 23, 59))

    st = StatsService(session)
    dec_now = local(2026, 12, 15)
    dec = period("month", dec_now, TZ)
    nov = period("prev_month", dec_now, TZ)
    assert (await st.totals(user, "KZT", dec.start, dec.end)).expense == Decimal("700.00")
    assert (await st.totals(user, "KZT", nov.start, nov.end)).expense == Decimal("300.00")
    day = period("day", local(2026, 12, 1, 10), TZ)
    assert (await st.totals(user, "KZT", day.start, day.end)).expense == Decimal("700.00")
    assert nov_tx.id != dec_tx.id


async def test_periods_follow_timezone_change(session, user):
    await add_tx(session, user, EXPENSE, 1000, "food", at=local(2026, 11, 18, 2, 0))  # 17.11 21:00 UTC
    st = StatsService(session)
    day = period("day", NOW, TZ)
    assert (await st.totals(user, "KZT", day.start, day.end)).expense == Decimal("1000.00")
    # В Лондоне (UTC+0) эта операция была 17.11 в 21:00 — сегодняшние расходы пусты
    london = get_tz("Europe/London")
    day_l = period("day", NOW, london)
    assert (await st.totals(user, "KZT", day_l.start, day_l.end)).expense == Decimal("0.00")


async def test_week_month_year_boundaries(session, user):
    await add_tx(session, user, EXPENSE, 1, "food", at=local(2026, 11, 15, 23, 59))  # вс — прошлая неделя
    await add_tx(session, user, EXPENSE, 10, "food", at=local(2026, 11, 16, 0, 0))  # пн — эта неделя
    await add_tx(session, user, EXPENSE, 100, "food", at=local(2026, 10, 31, 23, 59))  # прошлый месяц
    await add_tx(session, user, EXPENSE, 1000, "food", at=local(2025, 12, 31, 23, 59))  # прошлый год
    st = StatsService(session)

    async def exp(kind):
        p = period(kind, NOW, TZ)
        return (await st.totals(user, "KZT", p.start, p.end)).expense

    assert await exp("week") == Decimal("10.00")
    assert await exp("month") == Decimal("11.00")
    assert await exp("year") == Decimal("111.00")
    assert (await st.balances(user))["KZT"] == Decimal("-1111.00")


async def test_year_rollover_previous_month(session, user):
    jan = local(2027, 1, 10)
    prev = period("prev_month", jan, TZ)
    assert prev.start == local(2026, 12, 1, 0, 0) and prev.end == local(2027, 1, 1, 0, 0)


# ------------------------------------------------------------------ бюджеты и смена месяца


async def test_budget_resets_on_new_month(session, user):
    food = await CategoryService(session).by_code(user, EXPENSE, "food")
    svc = BudgetService(session)
    b, _ = await svc.upsert(user, BudgetInput(category_id=food.id, amount=Decimal("10000"), currency="KZT"))

    nov = local(2026, 11, 28)
    await add_tx(session, user, EXPENSE, 9000, "food", at=nov)
    assert (await svc.check_alerts(user, food.id, "KZT", nov)).threshold == 80

    dec = local(2026, 12, 2)
    prog = await svc.progress(user, b, dec)
    assert prog.spent == Decimal("0.00") and prog.percent == 0  # новый месяц — с нуля
    await add_tx(session, user, EXPENSE, 5500, "food", at=dec)
    ev = await svc.check_alerts(user, food.id, "KZT", dec)
    assert ev.threshold == 50  # пороги декабря независимы от ноября
    s = await StatsService(session).balance_summary(user, dec)
    assert s.budget_left == Decimal("4500.00")


async def test_editing_and_deleting_changes_budget_and_balance(session, user):
    food = await CategoryService(session).by_code(user, EXPENSE, "food")
    transport = await CategoryService(session).by_code(user, EXPENSE, "transport")
    bsvc, tsvc = BudgetService(session), TransactionService(session)
    b, _ = await bsvc.upsert(user, BudgetInput(category_id=food.id, amount=Decimal("1000"), currency="KZT"))
    tx = await add_tx(session, user, EXPENSE, 900, "food")
    assert (await bsvc.progress(user, b, NOW)).spent == Decimal("900.00")

    await tsvc.update(user, tx, TransactionUpdate(category_id=transport.id))
    assert (await bsvc.progress(user, b, NOW)).spent == Decimal("0.00")
    await tsvc.update(user, tx, TransactionUpdate(category_id=food.id, amount=Decimal("400")))
    assert (await bsvc.progress(user, b, NOW)).spent == Decimal("400.00")
    # Перенос даты в прошлый месяц убирает расход из текущего бюджета
    await tsvc.update(user, tx, TransactionUpdate(occurred_at=local(2026, 10, 20)))
    assert (await bsvc.progress(user, b, NOW)).spent == Decimal("0.00")

    await tsvc.delete(user, tx)
    assert await StatsService(session).balances(user) == {}
    assert (await tsvc.history(user, HistoryFilter())).total == 0


# ------------------------------------------------------------------ долги


async def test_partial_payments_exact_cents_close_debt(session, user):
    svc = DebtService(session)
    debt, _ = await svc.create(user, DebtCreate(direction=DebtDirection.I_OWE, person="Банк", amount=Decimal("100.00"),
                                                currency="USD", opened_on=date(2026, 11, 1)))  # fmt: skip
    for _ in range(3):
        await svc.pay(user, debt, Decimal("33.33"))
    assert debt.remaining == Decimal("0.01") and debt.status == "active"
    await svc.pay(user, debt, Decimal("0.01"))
    assert debt.remaining == Decimal("0.00") and debt.status == "closed"
    assert debt.paid == Decimal("100.00")


async def test_debt_payment_does_not_touch_balance(session, user):
    svc = DebtService(session)
    debt, _ = await svc.create(user, DebtCreate(direction=DebtDirection.OWED_TO_ME, person="Асан", amount=Decimal("5000"),
                                                currency="KZT", opened_on=date(2026, 11, 1)))  # fmt: skip
    await svc.pay(user, debt, Decimal("2000"))
    # Долги ведутся отдельно и не создают операций автоматически
    assert await StatsService(session).balances(user) == {}


# ------------------------------------------------------------------ повторы


async def test_same_token_after_edit_still_returns_original(session, user):
    tsvc = TransactionService(session)
    tx = await add_tx(session, user, EXPENSE, 100, "food", token="t-1")
    await tsvc.update(user, tx, TransactionUpdate(amount=Decimal("150")))
    again = await add_tx(session, user, EXPENSE, 100, "food", token="t-1")
    assert again.id == tx.id and again.amount == Decimal("150.00")


async def test_history_period_is_inclusive_in_user_timezone(session, user):
    from finance_bot.utils.dates import custom_period

    await add_tx(session, user, EXPENSE, 1, "food", at=local(2026, 11, 1, 0, 0))
    await add_tx(session, user, EXPENSE, 2, "food", at=local(2026, 11, 15, 23, 59))
    await add_tx(session, user, EXPENSE, 4, "food", at=local(2026, 11, 16, 0, 0))
    p = custom_period(date(2026, 11, 1), date(2026, 11, 15), TZ)
    page = await TransactionService(session).history(user, HistoryFilter(start=p.start, end=p.end))
    assert sorted(t.amount for t in page.items) == [Decimal("1.00"), Decimal("2.00")]


def test_now_fixture_sanity():
    # 12:00 по UTC+5 — это 07:00 UTC
    assert NOW.utcoffset() == timedelta(hours=5) and NOW == datetime(2026, 11, 18, 7, tzinfo=UTC)
