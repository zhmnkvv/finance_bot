from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import User, UserSettings


async def get_or_create_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    first_name: str | None,
    default_timezone: str,
    default_currency: str = "KZT",
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, chat_id=chat_id, first_name=first_name)
        user.settings = UserSettings(currency=default_currency, timezone=default_timezone)
        session.add(user)
        await session.flush()
        return user
    user.chat_id = chat_id
    user.is_active = True
    if first_name and user.first_name != first_name:
        user.first_name = first_name
    if user.settings is None:  # пользователь, перенесённый из старой версии без настроек
        user.settings = UserSettings(currency=default_currency, timezone=default_timezone)
        await session.flush()
    return user
