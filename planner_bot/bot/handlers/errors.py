"""Глобальный обработчик ошибок: логируем traceback, пользователю — понятный текст."""

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

log = logging.getLogger(__name__)

USER_ERROR_TEXT = "😔 Что-то пошло не так. Я уже записал ошибку — попробуй, пожалуйста, ещё раз."


async def on_error(event: ErrorEvent) -> bool:
    """Регистрируется на уровне Dispatcher (dp.errors), ловит ошибки всех роутеров."""
    log.exception("Unhandled error while processing update %s", event.update.update_id, exc_info=event.exception)
    try:
        if event.update.message:
            await event.update.message.answer(USER_ERROR_TEXT)
        elif event.update.callback_query:
            await event.update.callback_query.answer(USER_ERROR_TEXT, show_alert=True)
    except TelegramAPIError:
        log.warning("Could not notify user about the error")
    return True
