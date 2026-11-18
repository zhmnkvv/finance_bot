"""Надёжная отправка сообщений с обработкой ошибок Telegram API."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from finance_bot.db.models import User

log = logging.getLogger(__name__)


async def safe_send(bot: Bot, user: User, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    for attempt in range(2):
        try:
            await bot.send_message(user.chat_id, text, reply_markup=markup)
            return True
        except TelegramRetryAfter as e:
            log.warning("flood control, retry after %ss", e.retry_after)
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            log.info("user %s blocked the bot, deactivating", user.id)
            user.is_active = False
            return False
        except TelegramBadRequest as e:
            log.error("bad request sending to user %s: %s", user.id, e)
            return False
        except TelegramNetworkError as e:
            log.warning("network error sending to user %s (attempt %d): %s", user.id, attempt + 1, e)
            await asyncio.sleep(2)
    return False
