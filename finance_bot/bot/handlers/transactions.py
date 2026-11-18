"""Добавление доходов/расходов (пошагово и одной строкой), карточка операции, редактирование, удаление."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import STALE, drop_markup, moment_for, new_token, show, today_of, tz_of
from finance_bot.bot.states import AddTx, EditTx
from finance_bot.db.models import TxKind, User
from finance_bot.schemas.finance import TransactionCreate, TransactionUpdate
from finance_bot.services.budgets import BudgetService
from finance_bot.services.categories import CategoryService
from finance_bot.services.quick_input import parse_quick
from finance_bot.services.transactions import TransactionError, TransactionService
from finance_bot.utils.dates import now_utc, parse_user_date
from finance_bot.utils.money import AmountError, parse_amount

log = logging.getLogger(__name__)
router = Router(name="transactions")


# ------------------------------------------------------------------ пошаговое добавление


async def start_add(target: Message | CallbackQuery, state: FSMContext, user: User, kind: TxKind) -> None:
    await state.clear()
    await state.set_state(AddTx.amount)
    await state.update_data(kind=str(kind), token=new_token(), currency=user.settings.currency,
                            date=today_of(user).isoformat())  # fmt: skip
    title = "➕ <b>Новый доход</b>" if kind == TxKind.INCOME else "➖ <b>Новый расход</b>"
    await show(target, f"{title}\n\nВведи сумму, например <code>2500</code> или <code>1 999,90</code>:", kb.cancel_only())


@router.callback_query(kb.Menu.filter(F.action.in_({"income", "expense"})))
async def cb_start_add(cb: CallbackQuery, callback_data: kb.Menu, state: FSMContext, user: User) -> None:
    await start_add(cb, state, user, TxKind(callback_data.action))


@router.message(F.text.in_({kb.BTN_EXPENSE, kb.BTN_INCOME}))
async def msg_start_add(message: Message, state: FSMContext, user: User) -> None:
    await start_add(message, state, user, TxKind.EXPENSE if message.text == kb.BTN_EXPENSE else TxKind.INCOME)


@router.message(AddTx.amount, F.text)
async def add_amount(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        amount = parse_amount(message.text)
    except AmountError as e:
        await message.answer(f"⚠️ {e}", reply_markup=kb.cancel_only())
        return
    await state.update_data(amount=str(amount))
    await _ask_category(message, state, session, user)


async def _ask_category(target, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    cats = await CategoryService(session).list(user, data["kind"])
    await state.set_state(AddTx.category)
    await show(target, "📁 Выбери категорию:", kb.categories(cats))


@router.callback_query(AddTx.category, kb.Flow.filter(F.action == "cat"))
async def add_category(cb: CallbackQuery, callback_data: kb.Flow, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    cat = await CategoryService(session).get(user, callback_data.value, kind=data["kind"])
    if cat is None:
        await cb.answer("Категория не найдена", show_alert=True)
        return
    await state.update_data(cat_id=cat.id, cat_title=f"{cat.emoji} {cat.name}")
    if data.get("from_confirm"):
        await _show_confirm(cb, state, user)
        return
    await state.set_state(AddTx.comment)
    await show(cb, "💬 Добавь комментарий (например, «обед с коллегами») или пропусти:", kb.skip_cancel())


@router.message(AddTx.comment, F.text)
async def add_comment(message: Message, state: FSMContext, user: User) -> None:
    await state.update_data(comment=message.text.strip()[:255])
    await _show_confirm(message, state, user)


@router.callback_query(AddTx.comment, kb.Flow.filter(F.action == "skip"))
async def skip_comment(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await _show_confirm(cb, state, user)


async def _show_confirm(target, state: FSMContext, user: User) -> None:
    await state.set_state(AddTx.confirm)
    await state.update_data(from_confirm=True)
    await show(target, texts.tx_draft(await state.get_data(), today_of(user)), kb.confirm_tx())


@router.callback_query(AddTx.confirm, kb.Flow.filter(F.action == "chcat"))
async def confirm_change_cat(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await _ask_category(cb, state, session, user)


@router.callback_query(AddTx.confirm, kb.Flow.filter(F.action == "chdate"))
async def confirm_change_date(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTx.date)
    await show(cb, "📅 Укажи дату: <code>вчера</code>, <code>15.11</code> или <code>15.11.2026</code>", kb.date_choice())


@router.callback_query(AddTx.date, kb.Flow.filter(F.action.in_({"d_today", "d_yday"})))
async def pick_date_button(cb: CallbackQuery, callback_data: kb.Flow, state: FSMContext, user: User) -> None:
    today = today_of(user)
    d = today if callback_data.action == "d_today" else date.fromordinal(today.toordinal() - 1)
    await state.update_data(date=d.isoformat())
    await _show_confirm(cb, state, user)


@router.message(AddTx.date, F.text)
async def pick_date_text(message: Message, state: FSMContext, user: User) -> None:
    today = today_of(user)
    d = parse_user_date(message.text, today)
    if d is None:
        await message.answer("⚠️ Не понял дату. Пример: <code>15.11</code>", reply_markup=kb.date_choice())
        return
    if d > today:
        await message.answer("⚠️ Дата операции не может быть в будущем.", reply_markup=kb.date_choice())
        return
    await state.update_data(date=d.isoformat())
    await _show_confirm(message, state, user)


@router.callback_query(AddTx.confirm, kb.Flow.filter(F.action == "save"))
async def confirm_save(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    if "cat_id" not in data:
        await cb.answer(STALE, show_alert=True)
        return
    try:
        payload = TransactionCreate(
            kind=data["kind"],
            amount=Decimal(data["amount"]),
            currency=data["currency"],
            category_id=data["cat_id"],
            comment=data.get("comment"),
            occurred_at=moment_for(user, date.fromisoformat(data["date"])),
            client_token=data["token"],
        )
        tx, created = await TransactionService(session).create(user, payload)
    except (ValidationError, TransactionError) as e:
        log.warning("invalid transaction draft: %s", e)
        await cb.answer("Не удалось сохранить: проверь данные", show_alert=True)
        return
    await state.clear()
    if not created:
        await cb.answer("Эта операция уже сохранена ✅")
        return
    await show(cb, await saved_text(session, user, tx), _after_save_kb(tx.id, tx.kind))


async def saved_text(session: AsyncSession, user: User, tx) -> str:
    text = f"✅ <b>Сохранено</b>\n\n{texts.tx_line(tx, tz_of(user))}"
    if tx.kind == TxKind.EXPENSE:
        alert = await BudgetService(session).check_alerts(user, tx.category_id, tx.currency, now_utc())
        if alert:
            text += "\n\n" + texts.budget_alert(alert)
    return text


def _after_save_kb(tx_id: int, kind: str):
    again = kb.Menu(action="income" if kind == TxKind.INCOME else "expense")
    return kb.markup(
        [
            [kb.btn("➕ Ещё одну", again), kb.btn("✏️ Изменить", kb.TxCb(action="open", id=tx_id))],
            [kb.btn("💰 Баланс", kb.Menu(action="balance")), kb.HOME],
        ]
    )


# Кнопки сценария, нажатые вне сценария (например, после рестарта или повторно)
@router.callback_query(StateFilter(None), kb.Flow.filter(F.action.in_({"save", "cat", "skip", "chcat", "chdate"})))
async def stale_flow(cb: CallbackQuery) -> None:
    await cb.answer("Эта операция уже сохранена или отменена.")
    await drop_markup(cb)


# ------------------------------------------------------------------ быстрый ввод одной строкой


@router.message(StateFilter(None), F.text.regexp(r"^\s*[+\-]?\s*\d"))
async def quick_add(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        q = parse_quick(message.text)
    except AmountError as e:
        await message.answer(f"⚠️ {e}")
        return
    if q is None:
        await message.answer("Не понял 🤔 Формат: <code>500 кофе</code> или <code>+150000 зарплата</code>.")
        return
    await state.set_state(AddTx.confirm)
    await state.update_data(kind=str(q.kind), token=new_token(), currency=user.settings.currency,
                            date=today_of(user).isoformat(), amount=str(q.amount), comment=q.comment,
                            from_confirm=True)  # fmt: skip
    cat = await CategoryService(session).by_code(user, q.kind, q.category_code) if q.category_code else None
    if cat is None:
        await _ask_category(message, state, session, user)
        return
    await state.update_data(cat_id=cat.id, cat_title=f"{cat.emoji} {cat.name}")
    await _show_confirm(message, state, user)


# ------------------------------------------------------------------ карточка и редактирование


async def _get_tx(cb_or_msg, session, user, tx_id):
    tx = await TransactionService(session).get(user, tx_id)
    if tx is None and isinstance(cb_or_msg, CallbackQuery):
        await cb_or_msg.answer("Операция не найдена — возможно, уже удалена.", show_alert=True)
    return tx


async def show_tx(target, session: AsyncSession, user: User, tx_id: int, prefix: str = "") -> None:
    tx = await _get_tx(target, session, user, tx_id)
    if tx is not None:
        await show(target, prefix + texts.tx_card(tx, tz_of(user)), kb.tx_card(tx.id))


@router.callback_query(kb.TxCb.filter(F.action == "open"))
async def cb_open(cb: CallbackQuery, callback_data: kb.TxCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await show_tx(cb, session, user, callback_data.id)


@router.callback_query(kb.TxCb.filter(F.action == "amount"))
async def cb_edit_amount(cb: CallbackQuery, callback_data: kb.TxCb, state: FSMContext, session, user) -> None:
    if await _get_tx(cb, session, user, callback_data.id) is None:
        return
    await state.set_state(EditTx.amount)
    await state.update_data(tx_id=callback_data.id)
    await show(cb, "✏️ Введи новую сумму:", kb.markup([[kb.btn("◀️ Назад", kb.TxCb(action="open", id=callback_data.id))]]))


@router.message(EditTx.amount, F.text)
async def edit_amount(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        amount = parse_amount(message.text)
    except AmountError as e:
        await message.answer(f"⚠️ {e}")
        return
    await _apply_edit(message, state, session, user, TransactionUpdate(amount=amount))


@router.callback_query(kb.TxCb.filter(F.action == "comment"))
async def cb_edit_comment(cb: CallbackQuery, callback_data: kb.TxCb, state: FSMContext, session, user) -> None:
    if await _get_tx(cb, session, user, callback_data.id) is None:
        return
    await state.set_state(EditTx.comment)
    await state.update_data(tx_id=callback_data.id)
    await show(cb, "💬 Введи новый комментарий:", kb.markup([
        [kb.btn("🧹 Убрать комментарий", kb.Flow(action="clear_comment"))],
        [kb.btn("◀️ Назад", kb.TxCb(action="open", id=callback_data.id))],
    ]))  # fmt: skip


@router.message(EditTx.comment, F.text)
async def edit_comment(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await _apply_edit(message, state, session, user, TransactionUpdate(comment=message.text))


@router.callback_query(EditTx.comment, kb.Flow.filter(F.action == "clear_comment"))
async def clear_comment(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await _apply_edit(cb, state, session, user, TransactionUpdate(clear_comment=True))


@router.callback_query(kb.TxCb.filter(F.action == "date"))
async def cb_edit_date(cb: CallbackQuery, callback_data: kb.TxCb, state: FSMContext, session, user) -> None:
    if await _get_tx(cb, session, user, callback_data.id) is None:
        return
    await state.set_state(EditTx.date)
    await state.update_data(tx_id=callback_data.id)
    await show(cb, "📅 Введи дату: <code>вчера</code>, <code>15.11</code>…",
               kb.markup([[kb.btn("◀️ Назад", kb.TxCb(action="open", id=callback_data.id))]]))  # fmt: skip


@router.message(EditTx.date, F.text)
async def edit_date(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    today = today_of(user)
    d = parse_user_date(message.text, today)
    if d is None or d > today:
        await message.answer("⚠️ Нужна дата не в будущем, например <code>15.11</code>.")
        return
    await _apply_edit(message, state, session, user, TransactionUpdate(occurred_at=moment_for(user, d)))


@router.callback_query(kb.TxCb.filter(F.action == "cat"))
async def cb_edit_cat(cb: CallbackQuery, callback_data: kb.TxCb, session: AsyncSession, user: User) -> None:
    tx = await _get_tx(cb, session, user, callback_data.id)
    if tx is None:
        return
    cats = await CategoryService(session).list(user, tx.kind)
    await show(cb, "📁 Новая категория:", kb.categories(
        cats, cb_factory=lambda c: kb.TxCb(action="setcat", id=tx.id, value=c.id),
        extra=[[kb.btn("◀️ Назад", kb.TxCb(action="open", id=tx.id))]],
    ))  # fmt: skip


@router.callback_query(kb.TxCb.filter(F.action == "setcat"))
async def cb_set_cat(cb: CallbackQuery, callback_data: kb.TxCb, state: FSMContext, session, user) -> None:
    await state.update_data(tx_id=callback_data.id)
    await _apply_edit(cb, state, session, user, TransactionUpdate(category_id=callback_data.value))


async def _apply_edit(target, state: FSMContext, session: AsyncSession, user: User, upd: TransactionUpdate) -> None:
    data = await state.get_data()
    await state.clear()
    svc = TransactionService(session)
    tx = await svc.get(user, data.get("tx_id", 0))
    if tx is None:
        await show(target, "Операция не найдена.", kb.back_home())
        return
    try:
        await svc.update(user, tx, upd)
    except TransactionError as e:
        await show(target, f"⚠️ {escape(str(e))}", kb.tx_card(tx.id))
        return
    prefix = "✅ Изменено\n\n"
    if tx.kind == TxKind.EXPENSE:
        alert = await BudgetService(session).check_alerts(user, tx.category_id, tx.currency, now_utc())
        if alert:
            prefix += texts.budget_alert(alert) + "\n\n"
    await show(target, prefix + texts.tx_card(tx, tz_of(user)), kb.tx_card(tx.id))


@router.callback_query(kb.TxCb.filter(F.action == "del"))
async def cb_delete(cb: CallbackQuery, callback_data: kb.TxCb, session: AsyncSession, user: User) -> None:
    tx = await _get_tx(cb, session, user, callback_data.id)
    if tx is None:
        return
    await show(cb, f"🗑 Удалить операцию?\n\n{texts.tx_line(tx, tz_of(user))}",
               kb.yes_no(kb.TxCb(action="del_yes", id=tx.id), kb.TxCb(action="open", id=tx.id)))  # fmt: skip


@router.callback_query(kb.TxCb.filter(F.action == "del_yes"))
async def cb_delete_yes(cb: CallbackQuery, callback_data: kb.TxCb, session: AsyncSession, user: User) -> None:
    svc = TransactionService(session)
    tx = await svc.get(user, callback_data.id)
    if tx is None:  # повторное нажатие
        await show(cb, "🗑 Операция удалена.", kb.markup([[kb.btn("📁 К истории", kb.HistCb()), kb.HOME]]),
                   notice="Уже удалено.")
        return
    line = texts.tx_line(tx, tz_of(user))
    await svc.delete(user, tx)
    await show(cb, f"🗑 Удалено:\n{line}", kb.markup([[kb.btn("📁 К истории", kb.HistCb()), kb.HOME]]))

