from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.db.models import Category, TxKind, User


def visible_to(user: User):
    """Системные категории + личные категории этого пользователя."""
    return or_(Category.user_id.is_(None), Category.user_id == user.id)


class CategoryService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, user: User, kind: TxKind | str) -> list[Category]:
        res = await self.session.scalars(
            select(Category)
            .where(visible_to(user), Category.kind == str(kind))
            .order_by(Category.user_id.is_not(None), Category.sort_order, Category.id)
        )
        return list(res)

    async def get(self, user: User, category_id: int, kind: TxKind | str | None = None) -> Category | None:
        q = select(Category).where(Category.id == category_id, visible_to(user))
        if kind is not None:
            q = q.where(Category.kind == str(kind))
        return await self.session.scalar(q)

    async def by_code(self, user: User, kind: TxKind | str, code: str) -> Category | None:
        return await self.session.scalar(
            select(Category).where(visible_to(user), Category.kind == str(kind), Category.code == code)
        )
