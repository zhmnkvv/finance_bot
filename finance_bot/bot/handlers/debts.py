"""Долги: добавление, списки, карточка, частичное/полное погашение, удаление."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import new_token, show, today_of, tz_of
from finance_bot.bot.states import AddDebt, DebtPay
from finance_bot.db.models import DebtDirection, User
from finance_bot.schemas.finance import DebtCreate
from finance_bot.services.debts import DebtError, DebtService
from finance_bot.utils.dates import parse_user_date
from finance_bot.utils.money import AmountError, format_money, parse_amount

router = Router(name="debts")


def _menu_kb():
    return kb.markup([
        [kb.btn("➕ Я должен", kb.DebtCb(action="new_owe")), kb.btn("➕ Мне должны", kb.DebtCb(action="new_owed"))],
        [kb.btn("📋 Активные", kb.DebtCb(action="list")), kb.btn("🗂 Закрытые", kb.DebtCb(action="closed"))],
        [kb.HOME],
    ])  # fmt: skip


async def render_menu(target, session: AsyncSession, user: User, notice: str | None = None) -> None:
    debts = await DebtService(session).list(user, active=True)
    i_owe, owed = defaultdict(Decimal), defaultdict(Decimal)
    for d in debts:
        (i_owe if d.direction == DebtDirection.I_OWE else owed)[d.currency] += d.remaining
    fmt = lambda totals: ", ".join(format_money(v, c) for c, v in totals.items()) or "0"  # noqa: E731
    text = (
        "💳 <b>Долги</b>\n\n"
        f"📤 Я должен: <b>{fmt(i_owe)}</b>\n"
        f"📥 Мне должны: <b>{fmt(owed)}</b>\n"
        f"Активных долгов: {len(debts)}"
    )
    await show(target, text, _menu_kb(), notice=notice)


@router.callback_query(kb.Menu.filter(F.action == "debts"))
@router.callback_query(kb.DebtCb.filter(F.action == "menu"))
async def cb_menu(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await render_menu(cb, session, user)


@router.callback_query(kb.DebtCb.filter(F.action.in_({"list", "closed"})))
async def cb_list(cb: CallbackQuery, callback_data: kb.DebtCb, session: AsyncSession, user: User) -> None:
    active = callback_data.action == "list"
    debts = await DebtService(session).list(user, active=active)
    today = today_of(user)
    title = "📋 <b>Активные долги</b>" if active else "🗂 <b>Закрытые долги</b>"
    lines = [title, ""] + ([texts.debt_line(d, today) for d in debts] or ["Пусто."])
    rows = [[kb.btn(f"{'📤' if d.direction == DebtDirection.I_OWE else '📥'} {d.person[:25]} · "
                    f"{format_money(d.remaining if active else d.amount, d.currency)}",
                    kb.DebtCb(action="open", id=d.id))] for d in debts[:20]]  # fmt: skip
    rows.append([kb.btn("◀️ Долги", kb.DebtCb(action="menu")), kb.HOME])
    await show(cb, "\n".join(lines), kb.markup(rows))


# ------------------------------------------------------------------ добавление


@router.callback_query(kb.DebtCb.filter(F.action.in_({"new_owe", "new_owed"})))
async def cb_new(cb: CallbackQuery, callback_data: kb.DebtCb, state: FSMContext, user: User) -> None:
    direction = DebtDirection.I_OWE if callback_data.action == "new_owe" else DebtDirection.OWED_TO_ME
    await state.clear()
    await state.set_state(AddDebt.person)
    await state.update_data(direction=str(direction), token=new_token(), currency=user.settings.currency)
    q = "Кому ты должен?" if direction == DebtDirection.I_OWE else "Кто тебе должен?"
    await show(cb, f"💳 {q} Напиши имя:", kb.cancel_only())


@router.message(AddDebt.person, F.text)
async def debt_person(message: Message, state: FSMContext) -> None:
    person = " ".join(message.text.split())[:128]
    if not person:
        await message.answer("⚠️ Имя не может быть пустым.", reply_markup=kb.cancel_only())
        return
    await state.update_data(person=person)
    await state.set_state(AddDebt.amount)
    await message.answer("💰 Сумма долга:", reply_markup=kb.cancel_only())


@router.message(AddDebt.amount, F.text)
async def debt_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = parse_amount(message.text)
    except AmountError as e:
        await message.answer(f"⚠️ {e}", reply_markup=kb.cancel_only())
        return
    await state.update_data(amount=str(amount))
    await state.set_state(AddDebt.opened)
    await message.answer("📅 Когда возник долг? (<code>15.11</code>, <code>вчера</code>)",
                         reply_markup=kb.markup([[kb.btn("Сегодня", kb.Flow(action="d_today"))], [kb.CANCEL]]))  # fmt: skip


async def _ask_due(target, state: FSMContext) -> None:
    await state.set_state(AddDebt.due)
    await show(target, "⏰ До какого числа вернуть? (<code>30.11</code>) — я напомню заранее.",
               kb.markup([[kb.btn("Без срока", kb.Flow(action="skip"))], [kb.CANCEL]]))  # fmt: skip


@router.callback_query(AddDebt.opened, kb.Flow.filter(F.action == "d_today"))
async def debt_opened_today(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.update_data(opened=today_of(user).isoformat())
    await _ask_due(cb, state)


@router.message(AddDebt.opened, F.text)
async def debt_opened(message: Message, state: FSMContext, user: User) -> None:
    today = today_of(user)
    d = parse_user_date(message.text, today)
    if d is None or d > today:
        await message.answer("⚠️ Нужна дата не в будущем, например <code>15.11</code>.")
        return
    await state.update_data(opened=d.isoformat())
    await _ask_due(message, state)


async def _ask_comment(target, state: FSMContext) -> None:
    await state.set_state(AddDebt.comment)
    await show(target, "💬 Комментарий (необязательно):", kb.skip_cancel())


@router.callback_query(AddDebt.due, kb.Flow.filter(F.action == "skip"))
async def debt_no_due(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(due=None)
    await _ask_comment(cb, state)


@router.message(AddDebt.due, F.text)
async def debt_due(message: Message, state: FSMContext, user: User) -> None:
    d = parse_user_date(message.text, today_of(user))
    data = await state.get_data()
    if d is None:
        await message.answer("⚠️ Не понял дату. Пример: <code>30.11</code>")
        return
    if d < date.fromisoformat(data["opened"]):
        await message.answer("⚠️ Дата возврата не может быть раньше даты долга.")
        return
    await state.update_data(due=d.isoformat())
    await _ask_comment(message, state)


async def _confirm(target, state: FSMContext) -> None:
    await state.set_state(AddDebt.confirm)
    await show(target, texts.debt_draft(await state.get_data()),
               kb.markup([[kb.btn("✅ Сохранить", kb.Flow(action="save_debt"))], [kb.CANCEL]]))  # fmt: skip


@router.message(AddDebt.comment, F.text)
async def debt_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=message.text.strip()[:255])
    await _confirm(message, state)


@router.callback_query(AddDebt.comment, kb.Flow.filter(F.action == "skip"))
async def debt_skip_comment(cb: CallbackQuery, state: FSMContext) -> None:
    await _confirm(cb, state)


@router.callback_query(AddDebt.confirm, kb.Flow.filter(F.action == "save_debt"))
async def debt_save(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    try:
        debt, created = await DebtService(session).create(user, DebtCreate(
            direction=data["direction"], person=data["person"], amount=Decimal(data["amount"]),
            currency=data["currency"], opened_on=date.fromisoformat(data["opened"]),
            due_on=date.fromisoformat(data["due"]) if data.get("due") else None,
            comment=data.get("comment"), client_token=data["token"],
        ))  # fmt: skip
    except (ValidationError, DebtError) as e:
        await cb.answer(str(e)[:150] if isinstance(e, DebtError) else "Проверь данные", show_alert=True)
        return
    await state.clear()
    await show_card(cb, session, user, debt.id, prefix="✅ Долг сохранён\n\n" if created else "")


@router.callback_query(kb.Flow.filter(F.action == "save_debt"))
async def debt_save_stale(cb: CallbackQuery) -> None:
    await cb.answer("Этот долг уже сохранён или отменён.")


# ------------------------------------------------------------------ карточка и погашения


def _card_kb(debt) -> object:
    rows = []
    if debt.status == "active":
        rows.append([kb.btn("💵 Частично", kb.DebtCb(action="part", id=debt.id)),
                     kb.btn("✅ Полностью", kb.DebtCb(action="full", id=debt.id))])  # fmt: skip
    rows.append([kb.btn("🗑 Удалить", kb.DebtCb(action="del", id=debt.id))])
    rows.append([kb.btn("◀️ Долги", kb.DebtCb(action="menu")), kb.HOME])
    return kb.markup(rows)


async def show_card(target, session: AsyncSession, user: User, debt_id: int, prefix: str = "") -> None:
    debt = await DebtService(session).get(user, debt_id)
    if debt is None:
        if isinstance(target, CallbackQuery):
            await target.answer("Долг не найден", show_alert=True)
        return
    await show(target, prefix + texts.debt_card(debt, tz_of(user), today_of(user)), _card_kb(debt))


@router.callback_query(kb.DebtCb.filter(F.action == "open"))
async def cb_open(cb: CallbackQuery, callback_data: kb.DebtCb, state: FSMContext, session, user) -> None:
    await state.clear()
    await show_card(cb, session, user, callback_data.id)


@router.callback_query(kb.DebtCb.filter(F.action == "part"))
async def cb_part(cb: CallbackQuery, callback_data: kb.DebtCb, state: FSMContext, session, user) -> None:
    debt = await DebtService(session).get(user, callback_data.id)
    if debt is None or debt.status != "active":
        await cb.answer("Долг уже закрыт или не найден", show_alert=True)
        return
    await state.set_state(DebtPay.amount)
    await state.update_data(debt_id=debt.id, token=new_token())
    await show(cb, f"💵 Сколько погашено? Остаток: {format_money(debt.remaining, debt.currency)}", kb.cancel_only())


@router.message(DebtPay.amount, F.text)
async def pay_amount(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    svc = DebtService(session)
    debt = await svc.get(user, data.get("debt_id", 0))
    if debt is None:
        await state.clear()
        await message.answer("Долг не найден.", reply_markup=kb.back_home())
        return
    try:
        amount = parse_amount(message.text)
        await svc.pay(user, debt, amount, client_token=data["token"])
    except (AmountError, DebtError) as e:
        await message.answer(f"⚠️ {escape(str(e))}", reply_markup=kb.cancel_only())
        return
    await state.clear()
    prefix = "✅ Долг полностью погашен!\n\n" if debt.status == "closed" else f"✅ Погашение {format_money(amount, debt.currency)} записано\n\n"
    await show_card(message, session, user, debt.id, prefix=prefix)


@router.callback_query(kb.DebtCb.filter(F.action == "full"))
async def cb_full(cb: CallbackQuery, callback_data: kb.DebtCb, session: AsyncSession, user: User) -> None:
    debt = await DebtService(session).get(user, callback_data.id)
    if debt is None or debt.status != "active":
        await cb.answer("Долг уже закрыт", show_alert=True)
        return
    await show(cb, f"✅ Отметить долг «{escape(debt.person)}» погашенным полностью "
                   f"({format_money(debt.remaining, debt.currency)})?",
               kb.yes_no(kb.DebtCb(action="full_yes", id=debt.id), kb.DebtCb(action="open", id=debt.id),
                         yes_text="✅ Да, погашен"))  # fmt: skip


@router.callback_query(kb.DebtCb.filter(F.action == "full_yes"))
async def cb_full_yes(cb: CallbackQuery, callback_data: kb.DebtCb, session: AsyncSession, user: User) -> None:
    svc = DebtService(session)
    debt = await svc.get(user, callback_data.id)
    if debt is None:
        await cb.answer("Долг не найден", show_alert=True)
        return
    if debt.status == "active":
        # Токен на основе id долга: повторное нажатие не создаст второе погашение
        await svc.pay_full(user, debt, client_token=f"full:{debt.id}")
        await show_card(cb, session, user, debt.id, prefix="🎉 Долг закрыт!\n\n")
    else:
        await cb.answer("Долг уже закрыт")


@router.callback_query(kb.DebtCb.filter(F.action == "del"))
async def cb_del(cb: CallbackQuery, callback_data: kb.DebtCb, session: AsyncSession, user: User) -> None:
    debt = await DebtService(session).get(user, callback_data.id)
    if debt is None:
        await cb.answer("Долг не найден", show_alert=True)
        return
    await show(cb, f"🗑 Удалить долг «{escape(debt.person)}» вместе с историей погашений?",
               kb.yes_no(kb.DebtCb(action="del_yes", id=debt.id), kb.DebtCb(action="open", id=debt.id)))  # fmt: skip


@router.callback_query(kb.DebtCb.filter(F.action == "del_yes"))
async def cb_del_yes(cb: CallbackQuery, callback_data: kb.DebtCb, session: AsyncSession, user: User) -> None:
    svc = DebtService(session)
    debt = await svc.get(user, callback_data.id)
    if debt is not None:
        await svc.delete(user, debt)
    await render_menu(cb, session, user, notice="Удалено")
