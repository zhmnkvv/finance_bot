"""Системные категории. Создаются миграцией и (идемпотентно) при старте/в тестах."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Category, TxKind

EXPENSE_CATEGORIES = [
    ("food", "Еда", "🍔"),
    ("transport", "Транспорт", "🚕"),
    ("shopping", "Покупки", "🛍"),
    ("fun", "Развлечения", "🎉"),
    ("health", "Здоровье", "💊"),
    ("education", "Образование", "📚"),
    ("housing", "Жильё", "🏠"),
    ("subscriptions", "Подписки", "📺"),
    ("transfers", "Переводы", "💸"),
    ("other", "Другое", "📦"),
]

INCOME_CATEGORIES = [
    ("salary", "Зарплата", "💼"),
    ("scholarship", "Стипендия", "🎓"),
    ("freelance", "Фриланс", "💻"),
    ("business", "Бизнес", "🏢"),
    ("gifts", "Подарки", "🎁"),
    ("investments", "Инвестиции", "📈"),
    ("other", "Другое", "📦"),
]


def default_rows() -> list[dict]:
    rows = []
    for kind, items in ((TxKind.EXPENSE, EXPENSE_CATEGORIES), (TxKind.INCOME, INCOME_CATEGORIES)):
        for order, (code, name, emoji) in enumerate(items):
            rows.append(dict(user_id=None, kind=str(kind), code=code, name=name, emoji=emoji, sort_order=order))
    return rows


async def ensure_default_categories(session: AsyncSession) -> None:
    existing = {
        (k, c) for k, c in (await session.execute(select(Category.kind, Category.code).where(Category.user_id.is_(None))))
    }
    for row in default_rows():
        if (row["kind"], row["code"]) not in existing:
            session.add(Category(**row))
    await session.flush()
