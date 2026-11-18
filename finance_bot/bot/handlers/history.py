"""История операций: фильтры (тип, категория, период) и пагинация.

Состояние фильтра целиком живёт в callback_data — кнопки работают даже после рестарта бота.
"""

from datetime import date
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import show, today_of, tz_of
from finance_bot.bot.states import HistoryInput
from finance_bot.db.models import TxKind, User
from finance_bot.services.categories import CategoryService
from finance_bot.services.transactions import HistoryFilter, TransactionService
from finance_bot.utils.dates import custom_period, now_utc, parse_user_period, period

router = Router(name="history")

PAGE_SIZE = 8
KIND_MAP = {"a": None, "i": TxKind.INCOME, "e": TxKind.EXPENSE}
PERIOD_TITLES = {"all": "всё время", "day": "сегодня", "week": "неделя", "month": "месяц", "year": "год"}


def _filter(h: kb.HistCb, user: User) -> tuple[HistoryFilter, str]:
    tz = tz_of(user)
    flt = HistoryFilter(kind=KIND_MAP.get(h.kind), category_id=h.cat or None)
    label = PERIOD_TITLES.get(h.per, "")
    if h.per in ("day", "week", "month", "year"):
        p = period(h.per, now_utc(), tz)
        flt.start, flt.end = p.start, p.end
    elif h.per == "c" and h.a and h.b:
        p = custom_period(date.fromordinal(h.a), date.fromordinal(h.b), tz)
        flt.start, flt.end, label = p.start, p.end, p.label
    return flt, label


def _with(h: kb.HistCb, **kw) -> kb.HistCb:
    data = h.model_dump()
    data.update(kw)
    return kb.HistCb(**data)


async def render(target, session: AsyncSession, user: User, h: kb.HistCb) -> None:
    flt, period_label = _filter(h, user)
    page = await TransactionService(session).history(user, flt, h.page, PAGE_SIZE)
    tz = tz_of(user)

    cat_label = "все"
    if h.cat:
        cat = await CategoryService(session).get(user, h.cat)
        cat_label = f"{cat.emoji} {escape(cat.name)}" if cat else "все"
    kind_label = {"a": "все", "i": "доходы", "e": "расходы"}[h.kind]
    lines = [
        "📁 <b>История операций</b>",
        f"<i>Тип: {kind_label} · Категория: {cat_label} · Период: {period_label}</i>",
        "",
    ]
    if not page.items:
        lines.append("Операций не найдено.")
    lines += [texts.tx_line(tx, tz) for tx in page.items]
    if page.total:
        lines += ["", f"Всего: {page.total} · стр. {page.page + 1}/{page.pages}"]

    rows = [
        [
            kb.btn(("• " if h.kind == k else "") + t, _with(h, kind=k, cat=0 if k != h.kind else h.cat, page=0))
            for k, t in (("a", "Все"), ("i", "Доходы"), ("e", "Расходы"))
        ],
        [kb.btn("📁 Категория", _with(h, view="cats")), kb.btn("🗓 Период", _with(h, view="period"))],
    ]
    # Каждая операция — кнопка, открывающая карточку (редактирование/удаление)
    for tx in page.items:
        sign = "+" if tx.kind == TxKind.INCOME else "−"
        rows.append([kb.btn(f"{tx.category.emoji} {sign}{texts.format_money(tx.amount, tx.currency)} · "
                            f"{tx.occurred_at.astimezone(tz):%d.%m}", kb.TxCb(action="open", id=tx.id))])  # fmt: skip
    nav = []
    if page.page > 0:
        nav.append(kb.btn("◀️", _with(h, page=page.page - 1)))
    if page.pages > 1:
        nav.append(kb.btn(f"{page.page + 1}/{page.pages}", _with(h)))
    if page.page < page.pages - 1:
        nav.append(kb.btn("▶️", _with(h, page=page.page + 1)))
    if nav:
        rows.append(nav)
    rows.append([kb.HOME])
    await show(target, "\n".join(lines), kb.markup(rows))


@router.callback_query(kb.Menu.filter(F.action == "history"))
async def cb_history(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await render(cb, session, user, kb.HistCb())


@router.callback_query(kb.HistCb.filter(F.view == "list"))
async def cb_list(cb: CallbackQuery, callback_data: kb.HistCb, state: FSMContext, session, user) -> None:
    await state.clear()
    await render(cb, session, user, callback_data)


@router.callback_query(kb.HistCb.filter(F.view == "cats"))
async def cb_pick_category(cb: CallbackQuery, callback_data: kb.HistCb, session: AsyncSession, user: User) -> None:
    svc = CategoryService(session)
    kinds = [KIND_MAP[callback_data.kind]] if callback_data.kind != "a" else [TxKind.EXPENSE, TxKind.INCOME]
    buttons = []
    for kind in kinds:
        for c in await svc.list(user, kind):
            prefix = "➖" if kind == TxKind.EXPENSE else "➕"
            buttons.append(kb.btn(f"{prefix}{c.emoji} {c.name}", _with(callback_data, cat=c.id, page=0, view="list")))
    rows = kb.grid(buttons, 2)
    rows.append([kb.btn("Все категории", _with(callback_data, cat=0, page=0, view="list"))])
    await show(cb, "📁 Фильтр по категории:", kb.markup(rows))


@router.callback_query(kb.HistCb.filter(F.view == "period"))
async def cb_pick_period(cb: CallbackQuery, callback_data: kb.HistCb, state: FSMContext) -> None:
    opts = [("Сегодня", "day"), ("Неделя", "week"), ("Месяц", "month"), ("Год", "year"), ("Всё время", "all")]
    rows = kb.grid([kb.btn(t, _with(callback_data, per=p, a=0, b=0, page=0, view="list")) for t, p in opts], 3)
    rows.append([kb.btn("✍️ Свой период", _with(callback_data, view="custom"))])
    await show(cb, "🗓 Выбери период:", kb.markup(rows))


@router.callback_query(kb.HistCb.filter(F.view == "custom"))
async def cb_custom_period(cb: CallbackQuery, callback_data: kb.HistCb, state: FSMContext) -> None:
    await state.set_state(HistoryInput.period)
    await state.update_data(hist=callback_data.model_dump())
    await show(cb, "✍️ Введи период в формате <code>01.11.2026 - 15.11.2026</code> (можно без года):", kb.cancel_only())


@router.message(HistoryInput.period, F.text)
async def custom_period_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    rng = parse_user_period(message.text, today_of(user))
    if rng is None:
        await message.answer("⚠️ Не понял период. Пример: <code>01.11 - 15.11</code>", reply_markup=kb.cancel_only())
        return
    data = (await state.get_data()).get("hist", {})
    await state.clear()
    h = kb.HistCb(**{**data, "per": "c", "a": rng[0].toordinal(), "b": rng[1].toordinal(), "page": 0, "view": "list"})
    await render(message, session, user, h)
