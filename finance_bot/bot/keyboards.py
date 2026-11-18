"""Клавиатуры и CallbackData. callback_data в Telegram ограничен 64 байтами — префиксы короткие."""

from __future__ import annotations

from datetime import date
from typing import Literal

from aiogram.filters.callback_data import CallbackData
from pydantic import Field
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from finance_bot.config import SUPPORTED_CURRENCIES
from finance_bot.db.models import Category
from finance_bot.utils.money import CURRENCY_NAMES

BTN_MENU = "🏠 Меню"
BTN_EXPENSE = "➖ Расход"
BTN_INCOME = "➕ Доход"
BTN_BALANCE = "💰 Баланс"


class Menu(CallbackData, prefix="m"):
    action: str


class Flow(CallbackData, prefix="f"):
    """Шаги пошаговых сценариев: выбор категории, пропуск, подтверждение."""

    action: str
    value: int = 0


class TxCb(CallbackData, prefix="tx"):
    action: str  # open|amount|cat|setcat|comment|date|del|del_yes
    id: int
    value: int = 0


MAX_ORDINAL = date.max.toordinal()


class HistCb(CallbackData, prefix="h"):
    """Фильтр истории целиком в callback_data. Значения строго ограничены:
    клиент может прислать произвольные данные, и они не должны ронять хендлер."""

    kind: Literal["a", "i", "e"] = "a"
    cat: int = Field(default=0, ge=0)
    per: Literal["all", "day", "week", "month", "year", "c"] = "all"
    a: int = Field(default=0, ge=0, le=MAX_ORDINAL)  # ordinal начала (для custom)
    b: int = Field(default=0, ge=0, le=MAX_ORDINAL)  # ordinal конца
    page: int = Field(default=0, ge=0, le=100_000)
    view: Literal["list", "cats", "period", "custom"] = "list"


class StatsCb(CallbackData, prefix="st"):
    action: str


class BudgetCb(CallbackData, prefix="b"):
    action: str  # list|add|cat|open|edit|del|del_yes
    id: int = 0


class DebtCb(CallbackData, prefix="d"):
    action: str  # menu|new_owe|new_owed|list|closed|open|part|full|full_yes|del|del_yes
    id: int = 0


class RemCb(CallbackData, prefix="r"):
    action: str  # list|new|kind|open|del|del_yes|done|snooze|off|record
    id: int = 0
    value: str = ""


class SetCb(CallbackData, prefix="s"):
    action: str  # menu|cur|setcur|tz|settz|tzman|notif|dtime
    value: str = ""


def btn(text: str, cb: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def grid(buttons: list[InlineKeyboardButton], per_row: int = 2) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


HOME = btn("🏠 Меню", Menu(action="home"))
CANCEL = btn("❌ Отмена", Flow(action="cancel"))


def reply_menu() -> ReplyKeyboardMarkup:
    """Постоянная нижняя клавиатура — быстрый доступ к самым частым действиям."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_EXPENSE), KeyboardButton(text=BTN_INCOME)],
            [KeyboardButton(text=BTN_BALANCE), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Например: 500 кофе",
    )


def main_menu() -> InlineKeyboardMarkup:
    return markup(
        [
            [btn("💰 Мой баланс", Menu(action="balance"))],
            [btn("➕ Добавить доход", Menu(action="income")), btn("➖ Добавить расход", Menu(action="expense"))],
            [btn("📊 Статистика", Menu(action="stats")), btn("📁 История", Menu(action="history"))],
            [btn("🎯 Мои бюджеты", Menu(action="budgets")), btn("💳 Долги", Menu(action="debts"))],
            [btn("🔔 Напоминания", Menu(action="reminders")), btn("⚙️ Настройки", Menu(action="settings"))],
        ]
    )


def back_home() -> InlineKeyboardMarkup:
    return markup([[HOME]])


def cancel_only() -> InlineKeyboardMarkup:
    return markup([[CANCEL]])


def skip_cancel(skip_text: str = "⏭ Пропустить") -> InlineKeyboardMarkup:
    return markup([[btn(skip_text, Flow(action="skip")), CANCEL]])


def categories(cats: list[Category], action: str = "cat", cb_factory=None, extra: list | None = None) -> InlineKeyboardMarkup:
    make = cb_factory or (lambda c: Flow(action=action, value=c.id))
    rows = grid([btn(f"{c.emoji} {c.name}", make(c)) for c in cats], 2)
    rows += extra if extra is not None else [[CANCEL]]
    return markup(rows)


def confirm_tx() -> InlineKeyboardMarkup:
    return markup(
        [
            [btn("✅ Сохранить", Flow(action="save"))],
            [btn("📁 Категория", Flow(action="chcat")), btn("📅 Дата", Flow(action="chdate"))],
            [CANCEL],
        ]
    )


def date_choice() -> InlineKeyboardMarkup:
    return markup([[btn("Сегодня", Flow(action="d_today")), btn("Вчера", Flow(action="d_yday"))], [CANCEL]])


def tx_card(tx_id: int) -> InlineKeyboardMarkup:
    return markup(
        [
            [btn("✏️ Сумма", TxCb(action="amount", id=tx_id)), btn("📁 Категория", TxCb(action="cat", id=tx_id))],
            [btn("💬 Комментарий", TxCb(action="comment", id=tx_id)), btn("📅 Дата", TxCb(action="date", id=tx_id))],
            [btn("🗑 Удалить", TxCb(action="del", id=tx_id))],
            [btn("📁 К истории", HistCb()), HOME],
        ]
    )


def yes_no(yes: CallbackData, no: CallbackData, yes_text: str = "🗑 Да, удалить") -> InlineKeyboardMarkup:
    return markup([[btn(yes_text, yes), btn("Нет", no)]])


def stats_menu() -> InlineKeyboardMarkup:
    return markup(
        [
            [btn("📅 Обзор по периодам", StatsCb(action="overview"))],
            [btn("🥧 Расходы по категориям", StatsCb(action="cats"))],
            [btn("📈 Сравнение месяцев", StatsCb(action="compare"))],
            [HOME],
        ]
    )


def currencies(action: str = "setcur") -> InlineKeyboardMarkup:
    rows = grid([btn(CURRENCY_NAMES[c], SetCb(action=action, value=c)) for c in SUPPORTED_CURRENCIES], 2)
    rows.append([btn("◀️ Назад", SetCb(action="menu"))])
    return markup(rows)


TIMEZONES = [
    ("Алматы / Астана", "Asia/Almaty"),
    ("Кызылорда", "Asia/Qyzylorda"),
    ("Актау / Атырау", "Asia/Aqtau"),
    ("Ташкент", "Asia/Tashkent"),
    ("Бишкек", "Asia/Bishkek"),
    ("Москва", "Europe/Moscow"),
    ("Берлин", "Europe/Berlin"),
    ("UTC", "UTC"),
]


def timezones() -> InlineKeyboardMarkup:
    rows = grid([btn(name, SetCb(action="settz", value=tz)) for name, tz in TIMEZONES], 2)
    rows.append([btn("✍️ Ввести вручную", SetCb(action="tzman"))])
    rows.append([btn("◀️ Назад", SetCb(action="menu"))])
    return markup(rows)
