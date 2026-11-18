"""Фиксированные события (пары, работа, встречи)."""

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.db.models import Event, User
from planner_bot.schemas.commands import EventDraft
from planner_bot.services.tasks import normalize_title
from planner_bot.utils.dates import day_bounds_utc, get_tz

log = logging.getLogger(__name__)


class EventService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_between(self, user: User, start: datetime, end: datetime) -> list[Event]:
        res = await self.session.scalars(
            select(Event)
            .where(Event.user_id == user.id, Event.start_time < end, Event.end_time > start)
            .order_by(Event.start_time)
        )
        return list(res)

    async def list_for_day(self, user: User, day: date) -> list[Event]:
        start, end = day_bounds_utc(day, get_tz(user.timezone))
        return await self.list_between(user, start, end)

    async def create(self, user: User, draft: EventDraft) -> tuple[Event, bool, list[Event]]:
        """Возвращает (событие, создано_ли, пересекающиеся события)."""
        overlapping = await self.list_between(user, draft.start, draft.end)
        for ev in overlapping:
            if normalize_title(ev.title) == normalize_title(draft.title) and ev.start_time == draft.start:
                return ev, False, []
        event = Event(
            user_id=user.id,
            title=draft.title,
            start_time=draft.start,
            end_time=draft.end,
            description=draft.description,
        )
        self.session.add(event)
        await self.session.flush()
        log.info("event created id=%s user=%s", event.id, user.id)
        return event, True, overlapping
