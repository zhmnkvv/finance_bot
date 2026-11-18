"""Деньги: разбор ввода пользователя и форматирование. Только Decimal."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CURRENCY_SYMBOLS = {"KZT": "₸", "USD": "$", "EUR": "€", "RUB": "₽"}
CURRENCY_NAMES = {"KZT": "Тенге (₸)", "USD": "Доллар ($)", "EUR": "Евро (€)", "RUB": "Рубль (₽)"}

MAX_AMOUNT = Decimal("999999999999.99")  # влезает в Numeric(14, 2)
CENT = Decimal("0.01")


class AmountError(ValueError):
    """Некорректная сумма; текст понятен пользователю."""


_MULTIPLIERS = {"к": 1_000, "k": 1_000, "тыс": 1_000, "млн": 1_000_000, "m": 1_000_000}
_AMOUNT_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(к|k|тыс|млн|m)?\.?$", re.IGNORECASE)


def parse_amount(text: str, *, allow_zero: bool = False) -> Decimal:
    """'1500' / '1 500,50' / '2.5к' / '15000 ₸' → Decimal('1500.00'). Отрицательные запрещены."""
    raw = (text or "").strip().lower()
    for sym in (*CURRENCY_SYMBOLS.values(), "тг", "тенге", "руб", "kzt", "usd", "eur", "rub"):
        raw = raw.replace(sym, "")
    raw = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace("_", "")
    if raw.startswith("-"):
        raise AmountError("Сумма не может быть отрицательной.")
    raw = raw.lstrip("+")
    m = _AMOUNT_RE.match(raw)
    if not m:
        raise AmountError("Не понял сумму. Введи число, например: 1500 или 2499,90")
    try:
        value = Decimal(m.group(1).replace(",", "."))
    except InvalidOperation as e:
        raise AmountError("Не понял сумму.") from e
    if m.group(2):
        value *= _MULTIPLIERS[m.group(2).lower()]
    if value != value.quantize(CENT) and not m.group(2):
        raise AmountError("Слишком много знаков после запятой (максимум 2).")
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value < 0 or (value == 0 and not allow_zero):
        raise AmountError("Сумма должна быть больше нуля.")
    if value > MAX_AMOUNT:
        raise AmountError("Слишком большая сумма.")
    return value


def format_money(amount: Decimal, currency: str = "KZT", *, sign: bool = False) -> str:
    """Decimal('150000.5') → '150 000,50 ₸'. Копейки показываем, только если они есть."""
    amount = Decimal(amount).quantize(CENT)
    prefix = ""
    if amount < 0:
        prefix, amount = "−", -amount
    elif sign and amount > 0:
        prefix = "+"
    whole, frac = divmod(amount, 1)
    whole_str = f"{int(whole):,}".replace(",", " ")
    frac_str = f",{int(frac * 100):02d}" if frac else ""
    return f"{prefix}{whole_str}{frac_str} {CURRENCY_SYMBOLS.get(currency, currency)}"
