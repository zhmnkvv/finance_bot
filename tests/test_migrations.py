"""Alembic: чистая установка и обновление базы старого AI Planner без потери данных."""

import sqlite3

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from finance_bot.db.models import Base
from finance_bot.db.session import run_migrations

LEGACY_DDL = """
CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
  first_name VARCHAR(128), timezone VARCHAR(64) NOT NULL, notifications_enabled BOOLEAN NOT NULL,
  is_active BOOLEAN NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);
CREATE TABLE tasks (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), title VARCHAR(255),
  status VARCHAR(16));
CREATE INDEX ix_tasks_user_id ON tasks (user_id);
CREATE TABLE events (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), title VARCHAR(255));
CREATE TABLE reminders (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), fire_at DATETIME);
CREATE INDEX ix_reminders_user_id ON reminders (user_id);
CREATE TABLE processed_messages (id INTEGER PRIMARY KEY, chat_id BIGINT, message_id BIGINT);
INSERT INTO users VALUES (7, 555, 555, 'Old', 'Europe/Moscow', 1, 1, '2026-11-01 10:00:00', '2026-11-01 10:00:00');
INSERT INTO tasks VALUES (1, 7, 'Лабораторная', 'pending');
"""


def _tables(path):
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def test_fresh_install_matches_models(tmp_path):
    db = tmp_path / "fresh.db"
    run_migrations(f"sqlite+aiosqlite:///{db}")
    assert {"users", "transactions", "budgets", "debts", "debt_payments", "reminders"} <= _tables(db)
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from categories where user_id is null").fetchone()[0] == 17
    # Схема после миграций совпадает с ORM-моделями (нет «забытых» колонок/таблиц)
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == [], diff


def test_upgrade_from_ai_planner_keeps_old_data(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as c:
        c.executescript(LEGACY_DDL)
    run_migrations(f"sqlite+aiosqlite:///{db}")

    tables = _tables(db)
    assert {"legacy_planner_users", "legacy_planner_tasks", "legacy_planner_events"} <= tables
    with sqlite3.connect(db) as c:
        assert c.execute("select title from legacy_planner_tasks").fetchone()[0] == "Лабораторная"
        # Пользователь перенесён вместе с часовым поясом
        assert c.execute("select telegram_id from users where id = 7").fetchone()[0] == 555
        assert c.execute("select timezone, currency from user_settings where user_id = 7").fetchone() == (
            "Europe/Moscow",
            "KZT",
        )
    # Повторный запуск — ничего не ломает
    run_migrations(f"sqlite+aiosqlite:///{db}")
