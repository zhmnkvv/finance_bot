import os
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("BOT_TOKEN", "123:test")

from finance_bot.db.models import Base, TxKind  # noqa: E402
from finance_bot.db.seed import ensure_default_categories  # noqa: E402
from finance_bot.schemas.finance import TransactionCreate  # noqa: E402
from finance_bot.services.categories import CategoryService  # noqa: E402
from finance_bot.services.transactions import TransactionService  # noqa: E402
from finance_bot.services.users import get_or_create_user  # noqa: E402
from finance_bot.utils.dates import get_tz  # noqa: E402

TZ_NAME = "Asia/Qyzylorda"
TZ = get_tz(TZ_NAME)


def local(y, m, d, hh=12, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


# «Сейчас» в тестах: среда, 18 ноября 2026, 12:00 по местному времени
NOW = local(2026, 11, 18, 12, 0)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await ensure_default_categories(s)
        await s.commit()
        yield s
    await engine.dispose()


async def _user(session, tg_id: int):
    return await get_or_create_user(
        session, telegram_id=tg_id, chat_id=tg_id, first_name=f"U{tg_id}", default_timezone=TZ_NAME
    )


@pytest.fixture
async def user(session):
    return await _user(session, 1)


@pytest.fixture
async def other_user(session):
    return await _user(session, 2)


async def add_tx(session, user, kind, amount, code, *, at=NOW, currency=None, comment=None, token=None):
    """Хелпер: создать операцию по коду категории."""
    cat = await CategoryService(session).by_code(user, kind, code)
    tx, _ = await TransactionService(session).create(
        user,
        TransactionCreate(
            kind=kind,
            amount=Decimal(str(amount)),
            currency=currency or user.settings.currency,
            category_id=cat.id,
            comment=comment,
            occurred_at=at,
            client_token=token,
        ),
    )
    return tx


INCOME, EXPENSE = TxKind.INCOME, TxKind.EXPENSE
