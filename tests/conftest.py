import os
from datetime import datetime, time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("BOT_TOKEN", "123:test")

from planner_bot.db.models import Base, User  # noqa: E402
from planner_bot.utils.dates import get_tz  # noqa: E402

TZ_NAME = "Asia/Qyzylorda"
TZ = get_tz(TZ_NAME)


def local(y, m, d, hh=0, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


# «Сейчас» в тестах: среда, 18 ноября 2026, 08:00 по местному времени
NOW = local(2026, 11, 18, 8, 0)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def user(session):
    u = User(
        telegram_id=1,
        chat_id=1,
        first_name="Test",
        timezone=TZ_NAME,
        morning_time=time(8, 0),
        evening_time=time(21, 30),
        day_start=time(8, 0),
        day_end=time(23, 0),
        reminder_minutes=15,
    )
    session.add(u)
    await session.flush()
    return u
