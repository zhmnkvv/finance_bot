from datetime import date, time, timedelta
from decimal import Decimal

import pytest

from finance_bot.db.models import DebtDirection, DebtStatus, ReminderRepeat
from finance_bot.schemas.finance import BudgetInput, DebtCreate, ReminderCreate
from finance_bot.services.budgets import BudgetService
from finance_bot.services.categories import CategoryService
from finance_bot.services.debts import DebtError, DebtService
from finance_bot.services.reminders import ReminderError, ReminderService
from finance_bot.services.settings import SettingsService
from finance_bot.services.stats import StatsService
from finance_bot.utils.dates import get_tz
from tests.conftest import EXPENSE, NOW, TZ, add_tx, local

# ------------------------------------------------------------------ бюджеты


async def _budget(session, user, code, amount, currency="KZT"):
    cat = await CategoryService(session).by_code(user, EXPENSE, code)
    b, _ = await BudgetService(session).upsert(user, BudgetInput(category_id=cat.id, amount=Decimal(amount), currency=currency))
    return b, cat


async def test_create_budget_and_progress(session, user):
    b, cat = await _budget(session, user, "food", "50000")
    await add_tx(session, user, EXPENSE, 20000, "food")
    await add_tx(session, user, EXPENSE, 5000, "food", at=local(2026, 10, 30))  # прошлый месяц не считается
    prog = await BudgetService(session).progress(user, b, NOW)
    assert prog.spent == Decimal("20000.00") and prog.left == Decimal("30000.00") and prog.percent == 40

    # Повторное создание по той же категории меняет лимит, а не плодит бюджеты
    b2, created = await BudgetService(session).upsert(user, BudgetInput(category_id=cat.id, amount=Decimal("60000"), currency="KZT"))
    assert not created and b2.id == b.id and b2.amount == Decimal("60000.00")
    assert len(await BudgetService(session).list(user)) == 1


async def test_budget_alerts_50_80_100_once(session, user):
    _, cat = await _budget(session, user, "food", "10000")
    svc = BudgetService(session)

    async def spend(amount):
        await add_tx(session, user, EXPENSE, amount, "food")
        ev = await svc.check_alerts(user, cat.id, "KZT", NOW)
        return ev.threshold if ev else None

    assert await spend(4000) is None  # 40%
    assert await spend(1500) == 50  # 55%
    assert await spend(500) is None  # 60% — 50% уже было
    assert await spend(2500) == 80  # 85%
    assert await spend(2000) == 100  # 105% — превышение
    assert await spend(100) is None  # больше не спамим


async def test_budget_jump_straight_over_limit(session, user):
    _, cat = await _budget(session, user, "fun", "1000")
    await add_tx(session, user, EXPENSE, 5000, "fun")
    ev = await BudgetService(session).check_alerts(user, cat.id, "KZT", NOW)
    assert ev.threshold == 100 and ev.progress.left == Decimal("-4000.00")


async def test_budget_edit_resets_alerts_and_delete(session, user):
    b, cat = await _budget(session, user, "food", "10000")
    svc = BudgetService(session)
    await add_tx(session, user, EXPENSE, 6000, "food")
    assert (await svc.check_alerts(user, cat.id, "KZT", NOW)).threshold == 50
    await svc.update_amount(user, b, Decimal("7000"))  # 6000/7000 = 85%
    assert (await svc.check_alerts(user, cat.id, "KZT", NOW)).threshold == 80
    await svc.delete(user, b)
    assert await svc.list(user) == []
    assert await svc.check_alerts(user, cat.id, "KZT", NOW) is None


async def test_budget_left_in_balance(session, user):
    await _budget(session, user, "food", "50000")
    await _budget(session, user, "transport", "20000")
    await add_tx(session, user, EXPENSE, 30000, "food")
    await add_tx(session, user, EXPENSE, 9000, "shopping")  # без бюджета — в остаток не входит
    s = await StatsService(session).balance_summary(user, NOW)
    assert s.budget_total == Decimal("70000.00") and s.budget_left == Decimal("40000.00")


async def test_budget_currency_separate(session, user):
    _, cat = await _budget(session, user, "food", "100", currency="USD")
    await add_tx(session, user, EXPENSE, 90000, "food")  # тенге не влияют на долларовый бюджет
    assert await BudgetService(session).check_alerts(user, cat.id, "USD", NOW) is None


async def test_budget_isolation(session, user, other_user):
    b, _ = await _budget(session, user, "food", "1000")
    assert await BudgetService(session).get(other_user, b.id) is None
    assert await BudgetService(session).list(other_user) == []


# ------------------------------------------------------------------ долги


def _debt(**kw):
    data = dict(direction=DebtDirection.OWED_TO_ME, person="Асан", amount=Decimal("20000"), currency="KZT",
                opened_on=date(2026, 11, 1), due_on=date(2026, 11, 25), comment="на билеты")  # fmt: skip
    data.update(kw)
    return DebtCreate(**data)


async def test_add_debt(session, user):
    debt, created = await DebtService(session).create(user, _debt(client_token="d1"))
    assert created and debt.status == DebtStatus.ACTIVE and debt.remaining == Decimal("20000.00")
    again, created2 = await DebtService(session).create(user, _debt(client_token="d1"))
    assert not created2 and again.id == debt.id
    assert [d.id for d in await DebtService(session).list(user)] == [debt.id]


async def test_debt_due_before_opened_rejected(session, user):
    with pytest.raises(DebtError):
        await DebtService(session).create(user, _debt(due_on=date(2026, 10, 1)))


async def test_partial_debt_payment(session, user):
    svc = DebtService(session)
    debt, _ = await svc.create(user, _debt())
    await svc.pay(user, debt, Decimal("5000"), client_token="p1")
    await svc.pay(user, debt, Decimal("5000"), client_token="p1")  # повторное нажатие — без дубля
    await svc.pay(user, debt, Decimal("3000"))
    assert debt.paid == Decimal("8000.00") and debt.remaining == Decimal("12000.00")
    assert debt.status == DebtStatus.ACTIVE
    assert [p.amount for p in debt.payments] == [Decimal("5000.00"), Decimal("3000.00")]
    with pytest.raises(DebtError):
        await svc.pay(user, debt, Decimal("12000.01"))


async def test_full_debt_payment(session, user):
    svc = DebtService(session)
    debt, _ = await svc.create(user, _debt(direction=DebtDirection.I_OWE))
    await svc.pay(user, debt, Decimal("2000"))
    await svc.pay_full(user, debt)
    assert debt.remaining == 0 and debt.status == DebtStatus.CLOSED and debt.closed_at is not None
    assert len(debt.payments) == 2
    assert await svc.list(user) == []
    assert [d.id for d in await svc.list(user, active=False)] == [debt.id]
    with pytest.raises(DebtError):
        await svc.pay(user, debt, Decimal("1"))


async def test_debt_due_reminders_schedule(session, user):
    svc = DebtService(session)
    debt, _ = await svc.create(user, _debt(due_on=date(2026, 11, 21)))
    assert [d for d, _ in await svc.due_for_reminder(user, date(2026, 11, 17))] == []  # за 4 дня — рано
    assert [n for _, n in await svc.due_for_reminder(user, date(2026, 11, 18))] == [3]
    assert [n for _, n in await svc.due_for_reminder(user, date(2026, 11, 21))] == [0]
    assert [n for _, n in await svc.due_for_reminder(user, date(2026, 11, 22))] == [-1]
    debt.last_reminded_on = date(2026, 11, 20)
    assert await svc.due_for_reminder(user, date(2026, 11, 20)) == []  # уже напоминали сегодня


async def test_debt_isolation(session, user, other_user):
    svc = DebtService(session)
    debt, _ = await svc.create(user, _debt())
    assert await svc.get(other_user, debt.id) is None
    with pytest.raises(DebtError):
        await svc.pay(other_user, debt, Decimal("1"))


# ------------------------------------------------------------------ напоминания


def _rem(**kw):
    data = dict(kind="internet", title="Оплатить интернет", amount=Decimal("7990"), currency="KZT",
                first_date=date(2026, 11, 25), local_time=time(10, 0), repeat=ReminderRepeat.MONTHLY)  # fmt: skip
    data.update(kw)
    return ReminderCreate(**data)


async def test_create_reminder_in_user_timezone(session, user):
    rem, created = await ReminderService(session).create(user, _rem(), NOW)
    assert created and rem.next_run_at == local(2026, 11, 25, 10, 0)
    assert rem.next_run_at.utcoffset() == timedelta(0)  # хранится в UTC


async def test_one_time_reminder_in_past_rejected(session, user):
    with pytest.raises(ReminderError):
        await ReminderService(session).create(user, _rem(first_date=date(2026, 11, 1), repeat=ReminderRepeat.NONE), NOW)


async def test_monthly_reminder_starting_in_past_moves_forward(session, user):
    rem, _ = await ReminderService(session).create(user, _rem(first_date=date(2026, 10, 31)), NOW)
    assert rem.next_run_at == local(2026, 11, 30, 10, 0)  # 31-е → последний день ноября


async def test_reminder_due_and_advance(session, user):
    svc = ReminderService(session)
    monthly, _ = await svc.create(user, _rem(first_date=date(2026, 1, 31)), NOW)
    once, _ = await svc.create(user, _rem(kind="other", title="Купить билет", first_date=date(2026, 11, 20),
                                          repeat=ReminderRepeat.NONE), NOW)  # fmt: skip
    weekly, _ = await svc.create(user, _rem(kind="subscription", title="Spotify", first_date=date(2026, 11, 19),
                                            repeat=ReminderRepeat.WEEKLY), NOW)  # fmt: skip
    assert monthly.next_run_at == local(2026, 11, 30, 10)

    at = local(2026, 12, 1, 0, 0)
    due = await svc.due(at)
    assert {r.id for r in due} == {monthly.id, once.id, weekly.id}
    for r in due:
        svc.advance(r, TZ, at)
    assert monthly.next_run_at == local(2026, 12, 31, 10)
    assert once.is_active is False
    assert weekly.next_run_at == local(2026, 12, 3, 10)  # 19.11 → … → первый четверг после 01.12
    assert await svc.due(at) == []


async def test_reminder_snooze_and_delete(session, user, other_user):
    svc = ReminderService(session)
    rem, _ = await svc.create(user, _rem(), NOW)
    await svc.snooze(user, rem, NOW, 60)
    assert rem.next_run_at == NOW + timedelta(hours=1)
    assert await svc.get(other_user, rem.id) is None
    await svc.delete(user, rem)
    assert await svc.list(user) == []


async def test_timezone_change_keeps_local_time(session, user):
    rem, _ = await ReminderService(session).create(user, _rem(), NOW)
    await SettingsService(session).set_timezone(user, "Europe/Moscow")
    msk = get_tz("Europe/Moscow")
    assert rem.next_run_at.astimezone(msk).hour == 10
    assert rem.next_run_at.astimezone(msk).date() == date(2026, 11, 25)
