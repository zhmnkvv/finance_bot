import asyncio
import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from finance_bot.db.seed import ensure_default_categories

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def create_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, pool_pre_ping=not url.startswith("sqlite"))
    if url.startswith("sqlite"):
        # Внешние ключи (ON DELETE CASCADE) в SQLite выключены по умолчанию
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def run_migrations(url: str) -> None:
    """alembic upgrade head (синхронно; env.py сам работает через async-движок)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


async def init_db(engine: AsyncEngine, factory: async_sessionmaker[AsyncSession], url: str) -> None:
    await asyncio.to_thread(run_migrations, url)
    async with factory() as session:
        await ensure_default_categories(session)
        await session.commit()
    log.info("database is up to date")
