"""Регрессия: первые сообщения нового пользователя обрабатываются параллельно и не должны падать."""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from finance_bot.db.models import Base, User
from finance_bot.db.session import create_engine
from finance_bot.services.users import get_or_create_user
from tests.conftest import TZ_NAME


async def _engine(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_concurrent_first_update_does_not_fail(tmp_path):
    engine, factory = await _engine(tmp_path)
    a, b = factory(), factory()
    try:
        # Апдейт B «не нашёл» пользователя, пока апдейт A его создавал и коммитил
        real_scalar = b.scalar
        calls = {"n": 0}

        async def stale_first_select(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return await real_scalar(*args, **kwargs)

        b.scalar = stale_first_select

        ua = await get_or_create_user(a, telegram_id=10, chat_id=10, first_name="A", default_timezone=TZ_NAME)
        await a.commit()
        ub = await get_or_create_user(b, telegram_id=10, chat_id=10, first_name="A", default_timezone=TZ_NAME)
        await b.commit()

        assert ua.id == ub.id
        assert ub.settings is not None and ub.settings.timezone == TZ_NAME
        async with factory() as s:
            assert await s.scalar(select(func.count(User.id))) == 1
    finally:
        await a.close()
        await b.close()
        await engine.dispose()


async def test_parallel_get_or_create(tmp_path):
    engine, factory = await _engine(tmp_path)

    async def one():
        async with factory() as s:
            u = await get_or_create_user(s, telegram_id=20, chat_id=20, first_name="B", default_timezone=TZ_NAME)
            await s.commit()
            return u.id

    ids = await asyncio.gather(*(one() for _ in range(5)))
    assert len(set(ids)) == 1
    await engine.dispose()
