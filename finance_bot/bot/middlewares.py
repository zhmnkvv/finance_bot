"""Сессия БД на апдейт + текущий пользователь (с настройками)."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finance_bot.services.users import get_or_create_user

log = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], default_timezone: str, default_currency: str
    ):
        self.session_factory = session_factory
        self.default_timezone = default_timezone
        self.default_currency = default_currency

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            tg_user = data.get("event_from_user")
            chat = data.get("event_chat")
            if tg_user is not None and not tg_user.is_bot:
                data["user"] = await get_or_create_user(
                    session,
                    telegram_id=tg_user.id,
                    chat_id=chat.id if chat else tg_user.id,
                    first_name=tg_user.first_name,
                    default_timezone=self.default_timezone,
                    default_currency=self.default_currency,
                )
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            await session.commit()
            return result
