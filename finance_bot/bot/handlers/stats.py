"""Статистика: обзор по периодам, распределение по категориям, сравнение месяцев, графики."""

import asyncio
import logging
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import show, tz_of
from finance_bot.db.models import User
from finance_bot.services import charts
from finance_bot.services.stats import StatsService
from finance_bot.utils.dates import now_utc, period
from finance_bot.utils.money import format_money

log = logging.getLogger(__name__)
router = Router(name="stats")

BACK = kb.markup([[kb.btn("◀️ Статистика", kb.Menu(action="stats")), kb.HOME]])


@router.callback_query(kb.Menu.filter(F.action == "stats"))
async def cb_stats(cb: CallbackQuery) -> None:
    await show(cb, "📊 <b>Статистика</b>\n\nЧто показать?", kb.stats_menu())


@router.callback_query(kb.StatsCb.filter(F.action == "overview"))
async def cb_overview(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    st, tz, cur, now = StatsService(session), tz_of(user), user.settings.currency, now_utc()
    blocks = []
    for kind, title in (("day", "Сегодня"), ("week", "Эта неделя"), ("month", "Этот месяц"), ("year", "Этот год")):
        p = period(kind, now, tz)
        blocks.append(texts.totals_block(title, await st.totals(user, cur, p.start, p.end), cur))
    await show(cb, "📅 <b>Обзор по периодам</b>\n\n" + "\n\n".join(blocks), BACK)


async def _send_chart(cb: CallbackQuery, render, *args, caption: str) -> None:
    try:
        png = await asyncio.to_thread(render, *args)  # matplotlib — CPU-bound, не блокируем event loop
        await cb.message.answer_photo(BufferedInputFile(png, "chart.png"), caption=caption)
    except Exception:
        log.exception("chart rendering failed")


@router.callback_query(kb.StatsCb.filter(F.action == "cats"))
async def cb_categories(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    st, cur = StatsService(session), user.settings.currency
    month = period("month", now_utc(), tz_of(user))
    shares = await st.by_category(user, cur, month.start, month.end)
    if not shares:
        await show(cb, f"🥧 <b>Расходы по категориям</b> · {month.label}\n\nРасходов пока нет.", BACK)
        return
    total = sum((s.amount for s in shares), Decimal("0"))
    text = (
        f"🥧 <b>Расходы по категориям</b> · {month.label}\n"
        f"Всего: <b>{format_money(total, cur)}</b>\n\n"
        f"{texts.category_shares(shares, cur)}\n\n"
        f"🏆 <b>Топ-5:</b>\n{texts.category_shares(shares, cur, limit=5)}"
    )
    await show(cb, text, BACK)
    await _send_chart(cb, charts.categories_pie, shares, f"Расходы · {month.label}", cur, caption="🥧 Распределение расходов")


@router.callback_query(kb.StatsCb.filter(F.action == "compare"))
async def cb_compare(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    st, cur, tz, now = StatsService(session), user.settings.currency, tz_of(user), now_utc()
    cur_p, prev_p = period("month", now, tz), period("prev_month", now, tz)
    cur_t = await st.totals(user, cur, cur_p.start, cur_p.end)
    prev_t = await st.totals(user, cur, prev_p.start, prev_p.end)

    diff = cur_t.expense - prev_t.expense
    if prev_t.expense:
        pct = diff * 100 / prev_t.expense
        trend = f"{'📈 больше' if diff > 0 else '📉 меньше'} на {format_money(abs(diff), cur)} ({abs(pct):.0f}%)"
    else:
        trend = "в прошлом месяце расходов не было"
    if diff == 0 and prev_t.expense:
        trend = "столько же, сколько в прошлом месяце"

    cur_cats = {s.category.id: s for s in await st.by_category(user, cur, cur_p.start, cur_p.end)}
    prev_cats = {s.category.id: s for s in await st.by_category(user, cur, prev_p.start, prev_p.end)}
    rows = []
    for cid in sorted(set(cur_cats) | set(prev_cats), key=lambda i: -(cur_cats[i].amount if i in cur_cats else 0)):
        c = (cur_cats.get(cid) or prev_cats.get(cid)).category
        a = cur_cats[cid].amount if cid in cur_cats else Decimal("0")
        b = prev_cats[cid].amount if cid in prev_cats else Decimal("0")
        arrow = "🔺" if a > b else "🔻" if a < b else "▫️"
        rows.append(f"{arrow} {c.emoji} {escape(c.name)}: {format_money(a, cur)} (было {format_money(b, cur)})")

    text = (
        "📈 <b>Сравнение месяцев</b>\n\n"
        f"{texts.totals_block(cur_p.label, cur_t, cur)}\n\n"
        f"{texts.totals_block(prev_p.label, prev_t, cur)}\n\n"
        f"Расходы в этом месяце {trend}."
    )
    if rows:
        text += "\n\n<b>По категориям:</b>\n" + "\n".join(rows[:10])
    await show(cb, text, BACK)
    series = await st.monthly_series(user, cur, now, months=6)
    if any(t.income or t.expense for _, t in series):
        await _send_chart(cb, charts.months_bars, series, cur, caption="📊 Доходы и расходы за 6 месяцев")
