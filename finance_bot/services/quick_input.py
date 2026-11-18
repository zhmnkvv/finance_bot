"""Быстрый ввод одной строкой без AI: «500 кофе», «+150000 зарплата», «1200 такси до дома».

Правила простые и предсказуемые: число в начале, знак «+» — доход, иначе расход;
категория угадывается по словарю ключевых слов, остальное — комментарий.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from finance_bot.db.models import TxKind
from finance_bot.utils.money import parse_amount

EXPENSE_KEYWORDS = {
    "food": ["еда", "продукт", "кофе", "обед", "ужин", "завтрак", "кафе", "ресторан", "магнум", "small", "пицц",
             "бургер", "доставк", "шаурм", "фастфуд", "хлеб", "перекус", "столов"],
    "transport": ["такси", "автобус", "метро", "бензин", "топливо", "транспорт", "проезд", "yandex go", "парковк",
                  "поезд", "самолет", "самолёт", "билет на"],
    "shopping": ["покупк", "одежд", "обув", "магазин", "kaspi", "wildberries", "ozon", "техник", "подарок"],
    "fun": ["кино", "развлеч", "игр", "концерт", "бар", "клуб", "боулинг", "театр", "отдых"],
    "health": ["аптек", "лекарств", "врач", "здоров", "стоматолог", "анализ", "больниц", "спортзал", "зал"],
    "education": ["курс", "книг", "учеб", "образован", "универ", "репетитор", "обучен"],
    "housing": ["аренд", "квартир", "коммунал", "свет", "газ", "вода", "жиль", "ремонт", "ипотек"],
    "subscriptions": ["подписк", "интернет", "связь", "мобильн", "spotify", "netflix", "youtube", "icloud", "телефон"],
    "transfers": ["перевод", "долг"],
}  # fmt: skip
INCOME_KEYWORDS = {
    "salary": ["зарплат", "зп", "аванс", "оклад"],
    "scholarship": ["стипенди"],
    "freelance": ["фриланс", "заказ", "проект"],
    "business": ["бизнес", "выручк", "продаж"],
    "gifts": ["подар"],
    "investments": ["дивиденд", "инвест", "процент", "депозит", "вклад"],
}

_LINE_RE = re.compile(
    r"^\s*([+\-]?)\s*"
    r"((?:\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,]\d+)?(?:\s?(?:к|k|тыс)(?![а-яёa-z]))?)"
    r"(?:\s+(.*))?\s*$",
    re.IGNORECASE,
)


@dataclass
class QuickEntry:
    kind: TxKind
    amount: Decimal
    category_code: str | None
    comment: str | None


def guess_category(text: str, kind: TxKind) -> str | None:
    low = text.lower()
    table = INCOME_KEYWORDS if kind == TxKind.INCOME else EXPENSE_KEYWORDS
    for code, words in table.items():
        if any(w in low for w in words):
            return code
    return None


def parse_quick(text: str) -> QuickEntry | None:
    """None — если строка не похожа на операцию. AmountError — если сумма явно некорректна."""
    m = _LINE_RE.match(text or "")
    if not m:
        return None
    sign, amount_raw, rest = m.groups()
    if sign == "-":
        sign = ""  # «-500 кофе» — это расход 500, а не отрицательная сумма
    amount = parse_amount(amount_raw)  # AmountError пробрасываем — хендлер покажет понятный текст
    rest = (rest or "").strip(" ,.-—")
    # Явная подсказка дохода словами: «150000 зарплата»
    kind = TxKind.INCOME if sign == "+" else TxKind.EXPENSE
    if not sign and guess_category(rest, TxKind.INCOME) and not guess_category(rest, TxKind.EXPENSE):
        kind = TxKind.INCOME
    return QuickEntry(kind=kind, amount=amount, category_code=guess_category(rest, kind), comment=rest or None)
