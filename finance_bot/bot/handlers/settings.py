"""Настройки: валюта, часовой пояс, уведомления, время напоминаний о долгах."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import show
from finance_bot.bot.states import SettingsInput
from finance_bot.db.models import User
from finance_bot.services.settings import SettingsError, SettingsService
from finance_bot.utils.dates import parse_hhmm

router = Router(name="settings")


def _settings_kb(user: User):
    notif = "🔕 Выключить уведомления" if user.settings.notifications_enabled else "🔔 Включить уведомления"
    return kb.markup([
        [kb.btn("💱 Валюта", kb.SetCb(action="cur")), kb.btn("🌍 Часовой пояс", kb.SetCb(action="tz"))],
        [kb.btn(notif, kb.SetCb(action="notif"))],
        [kb.btn("💳 Время напоминаний о долгах", kb.SetCb(action="dtime"))],
        [kb.HOME],
    ])  # fmt: skip


async def render(target, user: User, prefix: str = "") -> None:
    await show(target, prefix + texts.settings(user), _settings_kb(user))


@router.callback_query(kb.Menu.filter(F.action == "settings"))
@router.callback_query(kb.SetCb.filter(F.action == "menu"))
async def cb_menu(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await render(cb, user)


@router.callback_query(kb.SetCb.filter(F.action == "cur"))
async def cb_currency(cb: CallbackQuery) -> None:
    await show(cb, "💱 Основная валюта для новых операций и бюджетов:", kb.currencies())


@router.callback_query(kb.SetCb.filter(F.action == "setcur"))
async def cb_set_currency(cb: CallbackQuery, callback_data: kb.SetCb, session: AsyncSession, user: User) -> None:
    try:
        await SettingsService(session).set_currency(user, callback_data.value)
    except SettingsError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await render(cb, user, "✅ Валюта изменена\n\n")


@router.callback_query(kb.SetCb.filter(F.action == "tz"))
async def cb_tz(cb: CallbackQuery) -> None:
    await show(cb, "🌍 Выбери часовой пояс:", kb.timezones())


@router.callback_query(kb.SetCb.filter(F.action == "settz"))
async def cb_set_tz(cb: CallbackQuery, callback_data: kb.SetCb, session: AsyncSession, user: User) -> None:
    try:
        await SettingsService(session).set_timezone(user, callback_data.value)
    except SettingsError as e:
        await cb.answer(str(e), show_alert=True)
        return
    await render(cb, user, "✅ Часовой пояс изменён\n\n")


@router.callback_query(kb.SetCb.filter(F.action == "tzman"))
async def cb_tz_manual(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsInput.timezone)
    await show(cb, "✍️ Введи часовой пояс в формате IANA, например <code>Asia/Almaty</code>:", kb.cancel_only())


@router.message(SettingsInput.timezone, F.text)
async def tz_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        await SettingsService(session).set_timezone(user, message.text.strip())
    except SettingsError as e:
        await message.answer(f"⚠️ {escape(str(e))}", reply_markup=kb.cancel_only())
        return
    await state.clear()
    await render(message, user, "✅ Часовой пояс изменён\n\n")


@router.callback_query(kb.SetCb.filter(F.action == "notif"))
async def cb_notif(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    on = await SettingsService(session).toggle_notifications(user)
    await render(cb, user, "🔔 Уведомления включены\n\n" if on else "🔕 Уведомления выключены\n\n")


@router.callback_query(kb.SetCb.filter(F.action == "dtime"))
async def cb_dtime(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsInput.debt_time)
    await show(cb, "⏰ Во сколько напоминать о сроках долгов? (<code>10:00</code>)", kb.cancel_only())


@router.message(SettingsInput.debt_time, F.text)
async def dtime_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    t = parse_hhmm(message.text)
    if t is None:
        await message.answer("⚠️ Не понял время. Пример: <code>10:00</code>", reply_markup=kb.cancel_only())
        return
    await SettingsService(session).set_debt_reminder_time(user, t)
    await state.clear()
    await render(message, user, "✅ Время сохранено\n\n")
