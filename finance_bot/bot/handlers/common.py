"""Общие помощники для хендлеров."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from finance_bot.db.models import User
from finance_bot.utils.dates import get_tz, local_dt, now_utc

log = logging.getLogger(__name__)

STALE = "Эта кнопка устарела. Открой меню заново."


def tz_of(user: User) -> ZoneInfo:
    return get_tz(user.settings.timezone)


def today_of(user: User) -> date:
    return now_utc().astimezone(tz_of(user)).date()


def new_token() -> str:
    """Токен идемпотентности для одного сценария — защищает от двойного «Сохранить»."""
    return uuid.uuid4().hex


def moment_for(user: User, d: date) -> datetime:
    """Момент операции: «сегодня» — текущее время, другой день — полдень этого дня (в поясе пользователя)."""
    now = now_utc()
    if d == now.astimezone(tz_of(user)).date():
        return now
    return local_dt(d, time(12, 0), tz_of(user))


async def show(
    target: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None, *, notice: str | None = None
) -> None:
    """Для кнопки — редактируем сообщение (меню не засоряет чат), для текста — отправляем новое.

    На callback отвечаем ровно один раз (повторный answerCallbackQuery Telegram отклоняет);
    notice — короткое всплывающее уведомление.
    """
    if isinstance(target, CallbackQuery):
        try:
            await target.answer(notice)
        except TelegramBadRequest:
            log.debug("callback already answered or expired")
        msg = target.message
        try:
            await msg.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            # Сообщение нельзя отредактировать (фото, слишком старое) — шлём новое
        await msg.answer(text, reply_markup=kb)
        return
    await target.answer(text, reply_markup=kb)


async def drop_markup(cb: CallbackQuery) -> None:
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
