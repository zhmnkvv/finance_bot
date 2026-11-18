"""Тексты сообщений (HTML). Пользовательский ввод всегда экранируется."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from finance_bot.db.models import Debt, DebtDirection, Reminder, ReminderRepeat, Transaction, TxKind, User
from finance_bot.services.budgets import BudgetAlertEvent, BudgetProgress
from finance_bot.services.reminders import REMINDER_KINDS
from finance_bot.services.stats import BalanceSummary, CategoryShare, Totals
from finance_bot.utils.dates import fmt_date, human_date
from finance_bot.utils.money import CURRENCY_NAMES, format_money

KIND_TITLE = {TxKind.INCOME: "доход", TxKind.EXPENSE: "расход"}
REPEAT_TITLE = {ReminderRepeat.NONE: "однократно", ReminderRepeat.WEEKLY: "каждую неделю",
                ReminderRepeat.MONTHLY: "каждый месяц"}  # fmt: skip

WELCOME = (
    "👋 <b>Finance Tracker</b> — твой личный финансовый помощник.\n\n"
    "• Записывай доходы и расходы кнопками или одной строкой: <code>500 кофе</code>, "
    "<code>+150000 зарплата</code>\n"
    "• Смотри баланс, статистику и графики\n"
    "• Ставь бюджеты по категориям — я предупрежу на 50%, 80% и 100%\n"
    "• Веди долги и напоминания о платежах\n\n"
    "Выбери действие:"
)


def signed(amount: Decimal, kind: str, currency: str) -> str:
    return format_money(amount if kind == TxKind.INCOME else -amount, currency, sign=True)


def progress_bar(percent: int, width: int = 10) -> str:
    filled = min(width, max(0, round(percent / 100 * width)))
    return "▓" * filled + "░" * (width - filled)


def balance(s: BalanceSummary) -> str:
    c = s.currency
    lines = ["💰 <b>Мой баланс</b>", "", f"Текущий баланс: <b>{format_money(s.balance, c)}</b>"]
    if s.other_balances:
        others = ", ".join(format_money(v, cur) for cur, v in s.other_balances.items())
        lines.append(f"<i>В других валютах: {others}</i>")
    lines += [
        "",
        "📅 <b>Текущий месяц</b>",
        f"Доходы: {format_money(s.month_income, c, sign=True)}",
        f"Расходы: {format_money(-s.month_expense, c)}",
        f"Итог месяца: {format_money(s.month_income - s.month_expense, c, sign=True)}",
    ]
    if s.budget_total is not None:
        lines += ["", f"🎯 Остаток бюджета: <b>{format_money(s.budget_left, c)}</b> из {format_money(s.budget_total, c)}"]
    if s.top_category:
        t = s.top_category
        lines.append(
            f"🔥 Самая затратная категория: {t.category.emoji} {escape(t.category.name)} — "
            f"{format_money(t.amount, c)} ({t.share:.0%})"
        )
    return "\n".join(lines)


def tx_draft(data: dict, today: date) -> str:
    kind = data["kind"]
    d = date.fromisoformat(data["date"])
    lines = [
        f"{'➕' if kind == TxKind.INCOME else '➖'} <b>Новый {KIND_TITLE[kind]}</b>",
        "",
        f"Сумма: <b>{format_money(Decimal(data['amount']), data['currency'])}</b>",
        f"Категория: {data.get('cat_title', '—')}",
        f"Дата: {human_date(d, today)}",
    ]
    if data.get("comment"):
        lines.append(f"Комментарий: {escape(data['comment'])}")
    lines += ["", "Всё верно?"]
    return "\n".join(lines)


def tx_line(tx: Transaction, tz: ZoneInfo) -> str:
    d = tx.occurred_at.astimezone(tz)
    comment = f" — {escape(tx.comment)}" if tx.comment else ""
    return f"{d:%d.%m} {tx.category.emoji} {signed(tx.amount, tx.kind, tx.currency)} · {escape(tx.category.name)}{comment}"


def tx_card(tx: Transaction, tz: ZoneInfo) -> str:
    d = tx.occurred_at.astimezone(tz)
    lines = [
        f"{'➕ Доход' if tx.kind == TxKind.INCOME else '➖ Расход'} <b>#{tx.id}</b>",
        "",
        f"Сумма: <b>{signed(tx.amount, tx.kind, tx.currency)}</b>",
        f"Категория: {tx.category.emoji} {escape(tx.category.name)}",
        f"Дата: {d:%d.%m.%Y %H:%M}",
        f"Комментарий: {escape(tx.comment) if tx.comment else '—'}",
    ]
    return "\n".join(lines)


def budget_alert(ev: BudgetAlertEvent) -> str:
    p = ev.progress
    cat = f"{p.budget.category.emoji} {escape(p.budget.category.name)}"
    cur = p.budget.currency
    if ev.threshold >= 100:
        if p.spent > p.budget.amount:
            return (f"🚨 <b>Бюджет «{cat}» превышен!</b>\nПотрачено {format_money(p.spent, cur)} из "
                    f"{format_money(p.budget.amount, cur)}, перерасход {format_money(-p.left, cur)}.")  # fmt: skip
        return f"🚨 <b>Лимит бюджета «{cat}» достигнут</b>: {format_money(p.spent, cur)} из {format_money(p.budget.amount, cur)}."
    if ev.threshold >= 80:
        return f"⚠️ Бюджет «{cat}»: потрачено {p.percent}%. Осталось {format_money(p.left, cur)}."
    return f"ℹ️ Бюджет «{cat}»: потрачено {p.percent}% ({format_money(p.spent, cur)} из {format_money(p.budget.amount, cur)})."


def budget_line(p: BudgetProgress) -> str:
    b = p.budget
    mark = "🚨" if p.percent >= 100 else "⚠️" if p.percent >= 80 else "✅"
    return (
        f"{mark} {b.category.emoji} <b>{escape(b.category.name)}</b>\n"
        f"{progress_bar(p.percent)} {p.percent}%\n"
        f"{format_money(p.spent, b.currency)} из {format_money(b.amount, b.currency)} · "
        f"осталось {format_money(p.left, b.currency)}"
    )


def totals_block(title: str, t: Totals, currency: str) -> str:
    return (
        f"<b>{title}</b>\n"
        f"  Доходы: {format_money(t.income, currency, sign=True)}\n"
        f"  Расходы: {format_money(-t.expense, currency)}\n"
        f"  Разница: {format_money(t.diff, currency, sign=True)}"
    )


def category_shares(shares: list[CategoryShare], currency: str, limit: int | None = None) -> str:
    rows = []
    for i, s in enumerate(shares[:limit] if limit else shares, 1):
        rows.append(f"{i}. {s.category.emoji} {escape(s.category.name)} — {format_money(s.amount, currency)} ({s.share:.0%})")
    return "\n".join(rows)


def debt_title(d: Debt) -> str:
    arrow = "📤 Я должен" if d.direction == DebtDirection.I_OWE else "📥 Мне должен"
    return f"{arrow}: <b>{escape(d.person)}</b>"


def debt_line(d: Debt, today: date) -> str:
    due = ""
    if d.due_on:
        days = (d.due_on - today).days
        due = f" · до {fmt_date(d.due_on)}" + (" ❗️просрочен" if days < 0 else " ⏰" if days <= 3 else "")
    who = "Я → " if d.direction == DebtDirection.I_OWE else "← "
    return f"{who}{escape(d.person)}: {format_money(d.remaining, d.currency)}{due}"


def debt_card(d: Debt, tz: ZoneInfo, today: date) -> str:
    lines = [
        debt_title(d),
        "",
        f"Сумма долга: {format_money(d.amount, d.currency)}",
        f"Погашено: {format_money(d.paid, d.currency)}",
        f"Остаток: <b>{format_money(d.remaining, d.currency)}</b>",
        f"Дата долга: {fmt_date(d.opened_on)}",
        f"Вернуть до: {fmt_date(d.due_on) if d.due_on else 'без срока'}",
    ]
    if d.comment:
        lines.append(f"Комментарий: {escape(d.comment)}")
    if d.status == "closed":
        lines.append("\n✅ <b>Долг закрыт</b>")
    if d.payments:
        lines += ["", "<b>История погашений:</b>"]
        lines += [f"• {p.paid_at.astimezone(tz):%d.%m.%Y} — {format_money(p.amount, d.currency)}" for p in d.payments]
    return "\n".join(lines)


def debt_draft(data: dict) -> str:
    who = "Я должен" if data["direction"] == DebtDirection.I_OWE else "Мне должен"
    lines = [
        "💳 <b>Новый долг</b>",
        "",
        f"{who}: <b>{escape(data['person'])}</b>",
        f"Сумма: {format_money(Decimal(data['amount']), data['currency'])}",
        f"Дата долга: {fmt_date(date.fromisoformat(data['opened']))}",
        f"Вернуть до: {fmt_date(date.fromisoformat(data['due'])) if data.get('due') else 'без срока'}",
    ]
    if data.get("comment"):
        lines.append(f"Комментарий: {escape(data['comment'])}")
    return "\n".join(lines + ["", "Сохранить?"])


def debt_due_notice(d: Debt, days_left: int) -> str:
    action = "вернуть" if d.direction == DebtDirection.I_OWE else "получить"
    who = f"{escape(d.person)}"
    when = ("сегодня" if days_left == 0 else "завтра" if days_left == 1 else
            f"через {days_left} дн." if days_left > 0 else f"просрочено на {-days_left} дн.")  # fmt: skip
    return (f"💳 <b>Напоминание о долге</b>\n\nНужно {action}: {who} — {format_money(d.remaining, d.currency)}\n"
            f"Срок: {fmt_date(d.due_on)} ({when})")  # fmt: skip


def reminder_line(r: Reminder, tz: ZoneInfo) -> str:
    emoji = REMINDER_KINDS.get(r.kind, ("🔔",))[0]
    when = r.next_run_at.astimezone(tz)
    amount = f" · {format_money(r.amount, r.currency)}" if r.amount else ""
    return f"{emoji} {escape(r.title)}{amount}\n   {when:%d.%m.%Y %H:%M} · {REPEAT_TITLE.get(r.repeat, r.repeat)}"


def reminder_draft(data: dict) -> str:
    emoji = REMINDER_KINDS[data["kind"]][0]
    d = date.fromisoformat(data["date"])
    lines = [
        "🔔 <b>Новое напоминание</b>",
        "",
        f"{emoji} {escape(data['title'])}",
        f"Сумма: {format_money(Decimal(data['amount']), data['currency']) if data.get('amount') else '—'}",
        f"Когда: {fmt_date(d)} в {data['time']}",
        f"Повтор: {REPEAT_TITLE[data['repeat']]}",
    ]
    return "\n".join(lines + ["", "Сохранить?"])


def reminder_fire(r: Reminder) -> str:
    emoji = REMINDER_KINDS.get(r.kind, ("🔔",))[0]
    amount = f"\nСумма: <b>{format_money(r.amount, r.currency)}</b>" if r.amount else ""
    return f"🔔 <b>Напоминание</b>\n\n{emoji} {escape(r.title)}{amount}"


def settings(user: User) -> str:
    s = user.settings
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"💱 Основная валюта: {CURRENCY_NAMES.get(s.currency, s.currency)}\n"
        f"🌍 Часовой пояс: <code>{escape(s.timezone)}</code>\n"
        f"🔔 Уведомления: {'включены' if s.notifications_enabled else 'выключены'}\n"
        f"💳 Напоминания о долгах в {s.debt_reminder_time:%H:%M}\n\n"
        "<i>Смена валюты не пересчитывает старые операции: каждая хранится в своей валюте.</i>"
    )
