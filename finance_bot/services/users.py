from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.db.models import User


async def get_or_create_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    chat_id: int,
    first_name: str | None,
    default_timezone: str,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, chat_id=chat_id, first_name=first_name, timezone=default_timezone)
        session.add(user)
        await session.flush()
    else:
        user.chat_id = chat_id
        user.is_active = True
        if first_name and user.first_name != first_name:
            user.first_name = first_name
    return user
