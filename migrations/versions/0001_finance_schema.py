"""Finance Tracker schema (replaces AI Planner schema, keeping old data as legacy_* tables)

Revision ID: 0001_finance_schema
Revises:
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_finance_schema"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_TABLES = ["processed_messages", "reminders", "events", "tasks", "users"]

TS = sa.DateTime(timezone=True)
MONEY = sa.Numeric(14, 2)


def _timestamps():
    return [sa.Column("created_at", TS, nullable=False), sa.Column("updated_at", TS, nullable=False)]


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    # Старая схема AI Planner: не удаляем данные, а переименовываем таблицы в legacy_planner_*
    legacy = "tasks" in existing and "users" in existing
    if legacy:
        inspector = sa.inspect(bind)
        is_pg = bind.dialect.name == "postgresql"
        for name in LEGACY_TABLES:
            if name not in existing:
                continue
            # Индексы, обслуживающие UNIQUE-ограничения (PostgreSQL отдаёт их с duplicates_constraint),
            # удалить нельзя — они уйдут вместе с ограничением, а их имена с новыми не конфликтуют
            old_indexes = [
                ix["name"]
                for ix in inspector.get_indexes(name)
                if ix.get("name") and not ix.get("duplicates_constraint")
            ]
            op.rename_table(name, f"legacy_planner_{name}")
            # Имена индексов/PK/sequence не переименовываются вместе с таблицей и конфликтуют с новыми
            for ix in old_indexes:
                op.execute(f'DROP INDEX IF EXISTS "{ix}"')
            if is_pg:
                op.execute(f'ALTER INDEX IF EXISTS "{name}_pkey" RENAME TO "legacy_planner_{name}_pkey"')
                op.execute(f'ALTER SEQUENCE IF EXISTS "{name}_id_seq" RENAME TO "legacy_planner_{name}_id_seq"')

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("first_name", sa.String(128)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        *_timestamps(),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="KZT"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Qyzylorda"),
        sa.Column("notifications_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("debt_reminder_time", sa.Time, nullable=False, server_default="10:00:00"),
    )

    categories = op.create_table(
        "categories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("emoji", sa.String(8), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "kind", "code", name="uq_category_code"),
    )
    op.create_index("ix_categories_user_id", "categories", ["user_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("comment", sa.String(255)),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("client_token", sa.String(64)),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "client_token", name="uq_transaction_token"),
    )
    for col in ("user_id", "kind", "category_id", "occurred_at"):
        op.create_index(f"ix_transactions_{col}", "transactions", [col])

    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "category_id", "currency", name="uq_budget"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])

    op.create_table(
        "budget_alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("budget_id", sa.Integer, sa.ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("threshold", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("budget_id", "period", "threshold", name="uq_budget_alert"),
    )
    op.create_index("ix_budget_alerts_budget_id", "budget_alerts", ["budget_id"])

    op.create_table(
        "debts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("person", sa.String(128), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("opened_on", sa.Date, nullable=False),
        sa.Column("due_on", sa.Date),
        sa.Column("comment", sa.String(255)),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("closed_at", TS),
        sa.Column("last_reminded_on", sa.Date),
        sa.Column("client_token", sa.String(64)),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "client_token", name="uq_debt_token"),
    )
    op.create_index("ix_debts_user_id", "debts", ["user_id"])
    op.create_index("ix_debts_status", "debts", ["status"])

    op.create_table(
        "debt_payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("debt_id", sa.Integer, sa.ForeignKey("debts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("paid_at", TS, nullable=False),
        sa.Column("comment", sa.String(255)),
        sa.Column("client_token", sa.String(64)),
        sa.UniqueConstraint("debt_id", "client_token", name="uq_debt_payment_token"),
    )
    op.create_index("ix_debt_payments_debt_id", "debt_payments", ["debt_id"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("amount", MONEY),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("repeat", sa.String(8), nullable=False),
        sa.Column("local_time", sa.Time, nullable=False),
        sa.Column("anchor_day", sa.Integer, nullable=False),
        sa.Column("next_run_at", TS, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_sent_at", TS),
        sa.Column("debt_id", sa.Integer, sa.ForeignKey("debts.id", ondelete="SET NULL")),
        sa.Column("client_token", sa.String(64)),
        sa.Column("note", sa.Text),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "client_token", name="uq_reminder_token"),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    op.create_index("ix_reminders_next_run_at", "reminders", ["next_run_at"])
    op.create_index("ix_reminders_is_active", "reminders", ["is_active"])

    from finance_bot.db.seed import default_rows

    op.bulk_insert(categories, default_rows())

    # Переносим пользователей старого бота (без их задач), чтобы они не «потерялись»
    if legacy:
        op.execute(
            "INSERT INTO users (id, telegram_id, chat_id, first_name, is_active, created_at, updated_at) "
            "SELECT id, telegram_id, chat_id, first_name, is_active, created_at, updated_at "
            "FROM legacy_planner_users"
        )
        op.execute(
            "INSERT INTO user_settings (user_id, currency, timezone, notifications_enabled, debt_reminder_time) "
            "SELECT id, 'KZT', timezone, notifications_enabled, '10:00:00' FROM legacy_planner_users"
        )
        if bind.dialect.name == "postgresql":
            op.execute("SELECT setval('users_id_seq', COALESCE((SELECT MAX(id) FROM users), 1))")


def downgrade() -> None:
    for name in ["reminders", "debt_payments", "debts", "budget_alerts", "budgets", "transactions",
                 "categories", "user_settings", "users"]:  # fmt: skip
        op.drop_table(name)
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in reversed(LEGACY_TABLES):
        if f"legacy_planner_{name}" in existing:
            op.rename_table(f"legacy_planner_{name}", name)
