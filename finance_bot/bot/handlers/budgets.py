"""Бюджеты: список с прогрессом, создание, изменение лимита, удаление."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import show
from finance_bot.bot.states import BudgetInputState
from finance_bot.db.models import TxKind, User
from finance_bot.schemas.finance import BudgetInput
from finance_bot.services.budgets import BudgetError, BudgetService
from finance_bot.services.categories import CategoryService
from finance_bot.utils.dates import now_utc
from finance_bot.utils.money import AmountError, format_money, parse_amount

router = Router(name="budgets")


async def render_list(target, session: AsyncSession, user: User, notice: str | None = None) -> None:
    progress = await BudgetService(session).all_progress(user, now_utc())
    lines = ["🎯 <b>Мои бюджеты</b> (на месяц)", ""]
    if not progress:
        lines.append("Бюджетов пока нет. Задай лимит на категорию — я предупрежу на 50%, 80% и 100%.")
    lines += [texts.budget_line(p) + "\n" for p in progress]
    rows = [[kb.btn(f"{p.budget.category.emoji} {p.budget.category.name} · {p.percent}%",
                    kb.BudgetCb(action="open", id=p.budget.id))] for p in progress]  # fmt: skip
    rows += [[kb.btn("➕ Новый бюджет", kb.BudgetCb(action="add"))], [kb.HOME]]
    await show(target, "\n".join(lines).strip(), kb.markup(rows), notice=notice)


@router.callback_query(kb.Menu.filter(F.action == "budgets"))
@router.callback_query(kb.BudgetCb.filter(F.action == "list"))
async def cb_list(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await render_list(cb, session, user)


@router.callback_query(kb.BudgetCb.filter(F.action == "add"))
async def cb_add(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    cats = await CategoryService(session).list(user, TxKind.EXPENSE)
    await show(cb, "🎯 Для какой категории бюджет?", kb.categories(
        cats, cb_factory=lambda c: kb.BudgetCb(action="cat", id=c.id),
        extra=[[kb.btn("◀️ Назад", kb.BudgetCb(action="list"))]],
    ))  # fmt: skip


@router.callback_query(kb.BudgetCb.filter(F.action == "cat"))
async def cb_pick_cat(cb: CallbackQuery, callback_data: kb.BudgetCb, state: FSMContext, session, user) -> None:
    cat = await CategoryService(session).get(user, callback_data.id, kind=TxKind.EXPENSE)
    if cat is None:
        await cb.answer("Категория не найдена", show_alert=True)
        return
    await state.set_state(BudgetInputState.amount)
    await state.update_data(category_id=cat.id, budget_id=None)
    await show(cb, f"💰 Лимит на месяц для {cat.emoji} {escape(cat.name)} ({user.settings.currency}):",
               kb.cancel_only())  # fmt: skip


@router.callback_query(kb.BudgetCb.filter(F.action == "open"))
async def cb_open(cb: CallbackQuery, callback_data: kb.BudgetCb, state: FSMContext, session, user) -> None:
    await state.clear()
    svc = BudgetService(session)
    b = await svc.get(user, callback_data.id)
    if b is None:
        await cb.answer("Бюджет не найден", show_alert=True)
        return
    p = await svc.progress(user, b, now_utc())
    await show(cb, "🎯 <b>Бюджет</b>\n\n" + texts.budget_line(p), kb.markup([
        [kb.btn("✏️ Изменить лимит", kb.BudgetCb(action="edit", id=b.id)),
         kb.btn("🗑 Удалить", kb.BudgetCb(action="del", id=b.id))],
        [kb.btn("◀️ К бюджетам", kb.BudgetCb(action="list")), kb.HOME],
    ]))  # fmt: skip


@router.callback_query(kb.BudgetCb.filter(F.action == "edit"))
async def cb_edit(cb: CallbackQuery, callback_data: kb.BudgetCb, state: FSMContext, session, user) -> None:
    b = await BudgetService(session).get(user, callback_data.id)
    if b is None:
        await cb.answer("Бюджет не найден", show_alert=True)
        return
    await state.set_state(BudgetInputState.amount)
    await state.update_data(budget_id=b.id, category_id=b.category_id)
    await show(cb, f"✏️ Новый лимит (сейчас {format_money(b.amount, b.currency)}):", kb.cancel_only())


@router.message(BudgetInputState.amount, F.text)
async def amount_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        amount = parse_amount(message.text)
    except AmountError as e:
        await message.answer(f"⚠️ {e}", reply_markup=kb.cancel_only())
        return
    data = await state.get_data()
    await state.clear()
    svc = BudgetService(session)
    try:
        if data.get("budget_id"):
            b = await svc.get(user, data["budget_id"])
            if b is None:
                raise BudgetError("Бюджет не найден.")
            await svc.update_amount(user, b, amount)
            prefix = "✅ Лимит изменён."
        else:
            _, created = await svc.upsert(
                user, BudgetInput(category_id=data["category_id"], amount=amount, currency=user.settings.currency)
            )
            prefix = "✅ Бюджет создан." if created else "✅ Бюджет уже был — лимит обновлён."
    except BudgetError as e:
        await message.answer(f"⚠️ {escape(str(e))}", reply_markup=kb.back_home())
        return
    await message.answer(prefix)
    await render_list(message, session, user)


@router.callback_query(kb.BudgetCb.filter(F.action == "del"))
async def cb_delete(cb: CallbackQuery, callback_data: kb.BudgetCb, session: AsyncSession, user: User) -> None:
    b = await BudgetService(session).get(user, callback_data.id)
    if b is None:
        await cb.answer("Бюджет не найден", show_alert=True)
        return
    await show(cb, f"🗑 Удалить бюджет «{b.category.emoji} {escape(b.category.name)}»?",
               kb.yes_no(kb.BudgetCb(action="del_yes", id=b.id), kb.BudgetCb(action="open", id=b.id)))  # fmt: skip


@router.callback_query(kb.BudgetCb.filter(F.action == "del_yes"))
async def cb_delete_yes(cb: CallbackQuery, callback_data: kb.BudgetCb, session: AsyncSession, user: User) -> None:
    svc = BudgetService(session)
    b = await svc.get(user, callback_data.id)
    if b is not None:
        await svc.delete(user, b)
    await render_list(cb, session, user, notice="Бюджет удалён")
