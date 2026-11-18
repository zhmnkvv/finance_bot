"""Напоминания о платежах: создание пошагово, список, удаление, действия по кнопкам из уведомления."""

from datetime import date
from datetime import time as dtime
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import drop_markup, new_token, show, today_of, tz_of
from finance_bot.bot.handlers.transactions import saved_text
from finance_bot.bot.states import AddReminder
from finance_bot.db.models import ReminderRepeat, User
from finance_bot.schemas.finance import ReminderCreate, TransactionCreate
from finance_bot.services.categories import CategoryService
from finance_bot.services.reminders import KIND_TO_CATEGORY, REMINDER_KINDS, ReminderError, ReminderService
from finance_bot.services.transactions import TransactionService
from finance_bot.utils.dates import now_utc, parse_hhmm, parse_user_date
from finance_bot.utils.money import AmountError, parse_amount

router = Router(name="reminders")


async def render_list(target, session: AsyncSession, user: User, notice: str | None = None) -> None:
    items = await ReminderService(session).list(user)
    tz = tz_of(user)
    lines = ["🔔 <b>Напоминания</b>", ""] + ([texts.reminder_line(r, tz) for r in items] or ["Напоминаний пока нет."])
    rows = [[kb.btn(f"{REMINDER_KINDS.get(r.kind, ('🔔',))[0]} {r.title[:30]}", kb.RemCb(action="open", id=r.id))]
            for r in items[:20]]  # fmt: skip
    rows += [[kb.btn("➕ Новое напоминание", kb.RemCb(action="new"))], [kb.HOME]]
    await show(target, "\n".join(lines), kb.markup(rows), notice=notice)


@router.callback_query(kb.Menu.filter(F.action == "reminders"))
@router.callback_query(kb.RemCb.filter(F.action == "list"))
async def cb_list(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await render_list(cb, session, user)


@router.callback_query(kb.RemCb.filter(F.action == "new"))
async def cb_new(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await state.update_data(token=new_token(), currency=user.settings.currency)
    rows = kb.grid([kb.btn(f"{e} {t}", kb.RemCb(action="kind", value=k)) for k, (e, t) in REMINDER_KINDS.items()], 2)
    rows.append([kb.CANCEL])
    await show(cb, "🔔 О чём напомнить?", kb.markup(rows))


@router.callback_query(kb.RemCb.filter(F.action == "kind"))
async def cb_kind(cb: CallbackQuery, callback_data: kb.RemCb, state: FSMContext) -> None:
    if callback_data.value not in REMINDER_KINDS:
        await cb.answer()
        return
    data = await state.get_data()
    if "token" not in data:
        await state.update_data(token=new_token())
    await state.update_data(kind=callback_data.value, title=REMINDER_KINDS[callback_data.value][1])
    if callback_data.value == "other":
        await state.set_state(AddReminder.title)
        await show(cb, "✍️ Название напоминания (например, «Оплатить страховку»):", kb.cancel_only())
        return
    await _ask_amount(cb, state)


@router.message(AddReminder.title, F.text)
async def rem_title(message: Message, state: FSMContext) -> None:
    title = " ".join(message.text.split())[:128]
    if not title:
        await message.answer("⚠️ Название не может быть пустым.")
        return
    await state.update_data(title=title)
    await _ask_amount(message, state)


async def _ask_amount(target, state: FSMContext) -> None:
    await state.set_state(AddReminder.amount)
    await show(target, "💰 Сумма платежа (необязательно):", kb.skip_cancel())


@router.message(AddReminder.amount, F.text)
async def rem_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = parse_amount(message.text)
    except AmountError as e:
        await message.answer(f"⚠️ {e}", reply_markup=kb.skip_cancel())
        return
    await state.update_data(amount=str(amount))
    await _ask_date(message, state)


@router.callback_query(AddReminder.amount, kb.Flow.filter(F.action == "skip"))
async def rem_skip_amount(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(amount=None)
    await _ask_date(cb, state)


async def _ask_date(target, state: FSMContext) -> None:
    await state.set_state(AddReminder.date)
    await show(target, "📅 Дата первого напоминания: <code>25.11</code>, <code>завтра</code>…",
               kb.markup([[kb.btn("Завтра", kb.Flow(action="d_tomorrow"))], [kb.CANCEL]]))  # fmt: skip


@router.callback_query(AddReminder.date, kb.Flow.filter(F.action == "d_tomorrow"))
async def rem_date_tomorrow(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.update_data(date=date.fromordinal(today_of(user).toordinal() + 1).isoformat())
    await _ask_time(cb, state)


@router.message(AddReminder.date, F.text)
async def rem_date(message: Message, state: FSMContext, user: User) -> None:
    today = today_of(user)
    d = parse_user_date(message.text, today)
    if d is None:
        await message.answer("⚠️ Не понял дату. Пример: <code>25.11</code>")
        return
    if d < today:
        await message.answer("⚠️ Дата уже прошла. Укажи сегодняшнюю или будущую.")
        return
    await state.update_data(date=d.isoformat())
    await _ask_time(message, state)


async def _ask_time(target, state: FSMContext) -> None:
    await state.set_state(AddReminder.time)
    times = ["09:00", "10:00", "12:00", "18:00", "20:00"]
    rows = kb.grid([kb.btn(t, kb.RemCb(action="time", value=t.replace(":", "-"))) for t in times], 5)
    rows.append([kb.CANCEL])
    await show(target, "⏰ Во сколько напомнить? Выбери или напиши (<code>08:30</code>):", kb.markup(rows))


@router.callback_query(AddReminder.time, kb.RemCb.filter(F.action == "time"))
async def rem_time_btn(cb: CallbackQuery, callback_data: kb.RemCb, state: FSMContext) -> None:
    await state.update_data(time=callback_data.value.replace("-", ":"))
    await _ask_repeat(cb, state)


@router.message(AddReminder.time, F.text)
async def rem_time(message: Message, state: FSMContext) -> None:
    t = parse_hhmm(message.text)
    if t is None:
        await message.answer("⚠️ Не понял время. Пример: <code>08:30</code>")
        return
    await state.update_data(time=t.strftime("%H:%M"))
    await _ask_repeat(message, state)


async def _ask_repeat(target, state: FSMContext) -> None:
    await state.set_state(AddReminder.repeat)
    rows = [[kb.btn("Однократно", kb.RemCb(action="repeat", value=ReminderRepeat.NONE))],
            [kb.btn("Каждую неделю", kb.RemCb(action="repeat", value=ReminderRepeat.WEEKLY)),
             kb.btn("Каждый месяц", kb.RemCb(action="repeat", value=ReminderRepeat.MONTHLY))],
            [kb.CANCEL]]  # fmt: skip
    await show(target, "🔁 Как часто повторять?", kb.markup(rows))


@router.callback_query(AddReminder.repeat, kb.RemCb.filter(F.action == "repeat"))
async def rem_repeat(cb: CallbackQuery, callback_data: kb.RemCb, state: FSMContext) -> None:
    if callback_data.value not in set(ReminderRepeat):
        await cb.answer()
        return
    await state.update_data(repeat=callback_data.value)
    await state.set_state(AddReminder.confirm)
    await show(cb, texts.reminder_draft(await state.get_data()),
               kb.markup([[kb.btn("✅ Сохранить", kb.RemCb(action="save"))], [kb.CANCEL]]))  # fmt: skip


@router.callback_query(AddReminder.confirm, kb.RemCb.filter(F.action == "save"))
async def rem_save(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    try:
        h, m = map(int, data["time"].split(":"))
        rem, _ = await ReminderService(session).create(user, ReminderCreate(
            kind=data["kind"], title=data["title"],
            amount=Decimal(data["amount"]) if data.get("amount") else None,
            currency=data["currency"], first_date=date.fromisoformat(data["date"]),
            local_time=dtime(h, m), repeat=data["repeat"], client_token=data["token"],
        ), now_utc())  # fmt: skip
    except ReminderError as e:
        await cb.answer(str(e), show_alert=True)
        return
    except (ValidationError, KeyError, ValueError):
        await cb.answer("Проверь данные напоминания", show_alert=True)
        return
    await state.clear()
    await render_list(cb, session, user, notice="Напоминание сохранено ✅")


@router.callback_query(kb.RemCb.filter(F.action == "save"))
async def rem_save_stale(cb: CallbackQuery) -> None:
    await cb.answer("Напоминание уже сохранено или отменено.")


@router.callback_query(kb.RemCb.filter(F.action == "open"))
async def cb_open(cb: CallbackQuery, callback_data: kb.RemCb, session: AsyncSession, user: User) -> None:
    rem = await ReminderService(session).get(user, callback_data.id)
    if rem is None:
        await cb.answer("Напоминание не найдено", show_alert=True)
        return
    await show(cb, "🔔 <b>Напоминание</b>\n\n" + texts.reminder_line(rem, tz_of(user)), kb.markup([
        [kb.btn("🗑 Удалить", kb.RemCb(action="del", id=rem.id))],
        [kb.btn("◀️ Напоминания", kb.RemCb(action="list")), kb.HOME],
    ]))  # fmt: skip


@router.callback_query(kb.RemCb.filter(F.action == "del"))
async def cb_del(cb: CallbackQuery, callback_data: kb.RemCb, session: AsyncSession, user: User) -> None:
    rem = await ReminderService(session).get(user, callback_data.id)
    if rem is None:
        await cb.answer("Напоминание не найдено", show_alert=True)
        return
    await show(cb, f"🗑 Удалить напоминание «{escape(rem.title)}»?",
               kb.yes_no(kb.RemCb(action="del_yes", id=rem.id), kb.RemCb(action="open", id=rem.id)))  # fmt: skip


@router.callback_query(kb.RemCb.filter(F.action == "del_yes"))
async def cb_del_yes(cb: CallbackQuery, callback_data: kb.RemCb, session: AsyncSession, user: User) -> None:
    svc = ReminderService(session)
    rem = await svc.get(user, callback_data.id)
    if rem is not None:
        await svc.delete(user, rem)
    await render_list(cb, session, user, notice="Удалено")


# ------------------------------------------------------------------ кнопки в присланном напоминании


@router.callback_query(kb.RemCb.filter(F.action == "done"))
async def cb_done(cb: CallbackQuery) -> None:
    await cb.answer("Отлично! ✅")
    await drop_markup(cb)


@router.callback_query(kb.RemCb.filter(F.action == "snooze"))
async def cb_snooze(cb: CallbackQuery, callback_data: kb.RemCb, session: AsyncSession, user: User) -> None:
    svc = ReminderService(session)
    rem = await svc.get(user, callback_data.id)
    if rem is None:
        await cb.answer("Напоминание не найдено", show_alert=True)
        return
    if rem.repeat != ReminderRepeat.NONE:
        await cb.answer("Это повторяющееся напоминание — следующее придёт по расписанию.", show_alert=True)
        return
    await svc.snooze(user, rem, now_utc(), 60)
    await cb.answer("Напомню через час ⏰")
    await drop_markup(cb)


@router.callback_query(kb.RemCb.filter(F.action == "record"))
async def cb_record(cb: CallbackQuery, callback_data: kb.RemCb, session: AsyncSession, user: User) -> None:
    """«Записать операцию» из напоминания: сумма и категория берутся из напоминания."""
    rem = await ReminderService(session).get(user, callback_data.id)
    if rem is None or rem.amount is None:
        await cb.answer("Не получится записать автоматически", show_alert=True)
        return
    kind, code = KIND_TO_CATEGORY.get(rem.kind, ("expense", "other"))
    cat = await CategoryService(session).by_code(user, kind, code)
    # Токен привязан к конкретной отправке — повторное нажатие не создаст дубль
    token = f"rem:{rem.id}:{int(rem.last_sent_at.timestamp()) if rem.last_sent_at else 0}"
    tx, created = await TransactionService(session).create(user, TransactionCreate(
        kind=kind, amount=rem.amount, currency=rem.currency, category_id=cat.id,
        comment=rem.title, occurred_at=now_utc(), client_token=token,
    ))  # fmt: skip
    await drop_markup(cb)
    if not created:
        await cb.answer("Уже записано ✅")
        return
    await cb.answer("Записано ✅")
    await cb.message.answer(await saved_text(session, user, tx), reply_markup=kb.back_home())
