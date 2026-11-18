"""Проверка на настоящем PostgreSQL. Запускается, только если задан TEST_DATABASE_URL.

    TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/finance_test

Тест ПОЛНОСТЬЮ очищает схему public этой базы, поэтому имя базы обязано оканчиваться на _test.
"""

import asyncio
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from finance_bot.db.models import Base, DebtDirection, TxKind
from finance_bot.db.session import run_migrations

def _load_url() -> str:
    """Из переменной окружения или из .env.test (файл в .gitignore — пароль не попадёт в git)."""
    url = os.environ.get("TEST_DATABASE_URL", "")
    env_file = Path(__file__).resolve().parents[1] / ".env.test"
    if not url and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("TEST_DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    return url


URL = _load_url()

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")

LEGACY_DDL = [
    # Схема AI Planner в том виде, как её создавал create_all (PostgreSQL)
    """CREATE TABLE users (id SERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
       first_name VARCHAR(128), timezone VARCHAR(64) NOT NULL, morning_time TIME, evening_time TIME,
       notifications_enabled BOOLEAN NOT NULL, is_active BOOLEAN NOT NULL,
       created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)""",
    "CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id)",
    """CREATE TABLE tasks (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
       title VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL)""",
    "CREATE INDEX ix_tasks_user_id ON tasks (user_id)",
    "CREATE INDEX ix_tasks_status ON tasks (status)",
    """CREATE TABLE events (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
       title VARCHAR(255) NOT NULL, start_time TIMESTAMPTZ NOT NULL)""",
    "CREATE INDEX ix_events_user_id ON events (user_id)",
    "CREATE INDEX ix_events_start_time ON events (start_time)",
    """CREATE TABLE reminders (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
       task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, kind VARCHAR(16) NOT NULL,
       fire_at TIMESTAMPTZ NOT NULL)""",
    "CREATE INDEX ix_reminders_user_id ON reminders (user_id)",
    "CREATE INDEX ix_reminders_fire_at ON reminders (fire_at)",
    """CREATE TABLE processed_messages (id SERIAL PRIMARY KEY, chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
       CONSTRAINT uq_processed_message UNIQUE (chat_id, message_id))""",
    """INSERT INTO users (telegram_id, chat_id, first_name, timezone, notifications_enabled, is_active, created_at,
       updated_at) VALUES (555, 555, 'Old', 'Europe/Moscow', true, true, now(), now()),
       (777, 777, 'Old2', 'Asia/Almaty', false, true, now(), now())""",
    "INSERT INTO tasks (user_id, title, status) VALUES (1, 'Лабораторная', 'pending')",
    "INSERT INTO processed_messages (chat_id, message_id) VALUES (555, 1)",
]


def _guard():
    db = make_url(URL).database or ""
    if not db.endswith("_test"):
        pytest.fail(f"Refusing to wipe database '{db}': its name must end with _test")


async def _reset(engine):
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


async def _scalar(engine, sql, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


@pytest.fixture
async def pg():
    _guard()
    engine = create_async_engine(URL)
    await _reset(engine)
    yield engine
    await engine.dispose()


async def test_pg_version(pg):
    version = await _scalar(pg, "SHOW server_version_num")
    assert int(version) >= 150000


async def test_pg_fresh_install_and_rerun(pg):
    await asyncio.to_thread(run_migrations, URL)
    await asyncio.to_thread(run_migrations, URL)  # повторный запуск — без ошибок
    assert await _scalar(pg, "SELECT count(*) FROM categories WHERE user_id IS NULL") == 17
    assert await _scalar(pg, "SELECT version_num FROM alembic_version") == "0001_finance_schema"
    async with pg.connect() as conn:
        diff = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata))
    assert diff == [], diff


async def test_pg_legacy_upgrade_keeps_data(pg):
    async with pg.begin() as conn:
        for stmt in LEGACY_DDL:
            await conn.execute(text(stmt))

    await asyncio.to_thread(run_migrations, URL)

    tables = {r for r in (await _rows(pg, "SELECT tablename FROM pg_tables WHERE schemaname='public'"))}
    assert {"legacy_planner_users", "legacy_planner_tasks", "legacy_planner_events", "legacy_planner_reminders",
            "legacy_planner_processed_messages", "users", "transactions"} <= tables  # fmt: skip
    assert await _scalar(pg, "SELECT title FROM legacy_planner_tasks") == "Лабораторная"
    # Последовательности и PK переименованы, новые заняли свои имена
    seqs = set(await _rows(pg, "SELECT sequence_name FROM information_schema.sequences"))
    assert {"legacy_planner_users_id_seq", "legacy_planner_tasks_id_seq", "users_id_seq"} <= seqs
    idx = set(await _rows(pg, "SELECT indexname FROM pg_indexes WHERE schemaname='public'"))
    assert {"legacy_planner_users_pkey", "users_pkey", "ix_users_telegram_id", "uq_processed_message"} <= idx
    # Пользователи и их часовые пояса перенесены
    rows = await _rows(pg, "SELECT u.telegram_id || ':' || s.timezone || ':' || s.notifications_enabled FROM users u "
                           "JOIN user_settings s ON s.user_id = u.id ORDER BY u.id")  # fmt: skip
    assert rows == ["555:Europe/Moscow:true", "777:Asia/Almaty:false"]

    # Новые пользователи получают id после перенесённых (sequence выставлен через setval)
    async with pg.begin() as conn:
        new_id = (await conn.execute(text(
            "INSERT INTO users (telegram_id, chat_id, is_active, created_at, updated_at) "
            "VALUES (999, 999, true, now(), now()) RETURNING id"))).scalar()  # fmt: skip
    assert new_id == 3

    await asyncio.to_thread(run_migrations, URL)  # повторный запуск на обновлённой базе
    assert await _scalar(pg, "SELECT count(*) FROM legacy_planner_users") == 2


async def _rows(engine, sql):
    async with engine.connect() as conn:
        return [r[0] for r in await conn.execute(text(sql))]


async def test_pg_services_end_to_end(pg):
    """Бизнес-логика на asyncpg: Numeric/Decimal, GROUP BY, timestamptz, изоляция."""
    from finance_bot.db.seed import ensure_default_categories
    from finance_bot.schemas.finance import BudgetInput, DebtCreate
    from finance_bot.services.budgets import BudgetService
    from finance_bot.services.categories import CategoryService
    from finance_bot.services.debts import DebtService
    from finance_bot.services.stats import StatsService
    from finance_bot.services.transactions import HistoryFilter, TransactionService
    from finance_bot.services.users import get_or_create_user
    from tests.conftest import NOW, TZ_NAME, add_tx, local

    await asyncio.to_thread(run_migrations, URL)
    factory = async_sessionmaker(pg, expire_on_commit=False)
    async with factory() as s:
        await ensure_default_categories(s)
        u1 = await get_or_create_user(s, telegram_id=1, chat_id=1, first_name="A", default_timezone=TZ_NAME)
        u2 = await get_or_create_user(s, telegram_id=2, chat_id=2, first_name="B", default_timezone=TZ_NAME)
        await add_tx(s, u1, TxKind.INCOME, "250000.50", "salary")
        await add_tx(s, u1, TxKind.EXPENSE, "1999.99", "food", token="pg-1")
        await add_tx(s, u1, TxKind.EXPENSE, "1999.99", "food", token="pg-1")  # дубль
        await add_tx(s, u1, TxKind.EXPENSE, "15.25", "shopping", currency="USD")
        await add_tx(s, u1, TxKind.EXPENSE, 700, "food", at=local(2026, 12, 1, 0, 30))  # декабрь по местному
        await s.commit()

        st = StatsService(s)
        assert await st.balances(u1) == {"KZT": Decimal("247300.51"), "USD": Decimal("-15.25")}
        summary = await st.balance_summary(u1, NOW)
        assert summary.month_expense == Decimal("1999.99") and isinstance(summary.month_expense, Decimal)
        assert summary.top_category.category.code == "food"
        assert (await TransactionService(s).history(u1, HistoryFilter())).total == 4
        assert (await TransactionService(s).history(u2, HistoryFilter())).total == 0

        food = await CategoryService(s).by_code(u1, TxKind.EXPENSE, "food")
        await BudgetService(s).upsert(u1, BudgetInput(category_id=food.id, amount=Decimal("2500"), currency="KZT"))
        assert (await BudgetService(s).check_alerts(u1, food.id, "KZT", NOW)).threshold == 50

        debt, _ = await DebtService(s).create(u1, DebtCreate(direction=DebtDirection.I_OWE, person="Асан",
                                                             amount=Decimal("1000"), currency="KZT",
                                                             opened_on=date(2026, 11, 1)))  # fmt: skip
        await DebtService(s).pay(u1, debt, Decimal("400"), client_token="pp")
        await DebtService(s).pay(u1, debt, Decimal("400"), client_token="pp")
        await s.commit()
        assert debt.remaining == Decimal("600.00")
