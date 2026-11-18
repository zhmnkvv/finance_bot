from datetime import date, time
from decimal import Decimal

import pytest

from finance_bot.services.quick_input import parse_quick
from finance_bot.db.models import TxKind
from finance_bot.utils.dates import add_months, parse_hhmm, parse_user_date, parse_user_period, period
from finance_bot.utils.money import AmountError, format_money, parse_amount
from tests.conftest import NOW, TZ, local


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1500", "1500.00"),
        ("1 500", "1500.00"),
        ("2499,90", "2499.90"),
        ("2499.9", "2499.90"),
        ("15000 ₸", "15000.00"),
        ("2.5к", "2500.00"),
        ("3 тыс", "3000.00"),
        ("+700", "700.00"),
    ],
)
def test_parse_amount_ok(text, expected):
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["-500", "0", "abc", "", "1.234", "10e5", "1" * 20])
def test_parse_amount_rejects_bad_input(text):
    with pytest.raises(AmountError):
        parse_amount(text)


def test_amount_is_decimal_not_float():
    assert isinstance(parse_amount("0.1"), Decimal)
    assert parse_amount("0.1") + parse_amount("0.2") == Decimal("0.30")


def test_format_money():
    assert format_money(Decimal("150000")) == "150 000 ₸"
    assert format_money(Decimal("1234.5"), "USD") == "1 234,50 $"
    assert format_money(Decimal("-200"), "EUR") == "−200 €"
    assert format_money(Decimal("10"), "RUB", sign=True) == "+10 ₽"


def test_user_dates():
    today = date(2026, 11, 18)
    assert parse_user_date("сегодня", today) == today
    assert parse_user_date("вчера", today) == date(2026, 11, 17)
    assert parse_user_date("25.12", today) == date(2026, 12, 25)
    assert parse_user_date("01.02.2027", today) == date(2027, 2, 1)
    assert parse_user_date("31.02", today) is None
    assert parse_user_period("01.11 - 15.11", today) == (date(2026, 11, 1), date(2026, 11, 15))
    assert parse_hhmm("9") == time(9) and parse_hhmm("25:00") is None


def test_add_months_clamps_end_of_month():
    assert add_months(date(2026, 1, 31), 1, 31) == date(2026, 2, 28)
    assert add_months(date(2026, 2, 28), 1, 31) == date(2026, 3, 31)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def test_periods_use_user_timezone():
    month = period("month", NOW, TZ)
    assert month.start == local(2026, 11, 1, 0, 0)
    assert month.end == local(2026, 12, 1, 0, 0)
    week = period("week", NOW, TZ)
    assert week.start == local(2026, 11, 16, 0, 0)  # понедельник
    prev = period("prev_month", NOW, TZ)
    assert prev.start == local(2026, 10, 1, 0, 0) and prev.end == month.start


@pytest.mark.parametrize(
    "text,kind,amount,code",
    [
        ("500 кофе", TxKind.EXPENSE, "500", "food"),
        ("1 200 такси до дома", TxKind.EXPENSE, "1200", "transport"),
        ("+150000 зарплата", TxKind.INCOME, "150000", "salary"),
        ("150000 зарплата", TxKind.INCOME, "150000", "salary"),
        ("-3000 аптека", TxKind.EXPENSE, "3000", "health"),
        ("2к кино", TxKind.EXPENSE, "2000", "fun"),
        ("999", TxKind.EXPENSE, "999", None),
    ],
)
def test_quick_input(text, kind, amount, code):
    q = parse_quick(text)
    assert (q.kind, q.amount, q.category_code) == (kind, Decimal(amount), code)


def test_quick_input_not_a_transaction():
    assert parse_quick("привет") is None
    assert parse_quick("кофе 500") is None
