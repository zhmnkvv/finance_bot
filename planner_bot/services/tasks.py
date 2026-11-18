"""CRUD задач и поиск по названию."""

from __future__ import annotations

import difflib
import logging
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.db.models import OPEN_STATUSES, Reminder, Task, TaskStatus, User
from planner_bot.schemas.commands import TaskDraft
from planner_bot.utils.dates import get_tz, now_utc

log = logging.getLogger(__name__)


def normalize_title(title: str) -> str:
    return " ".join(title.lower().replace("ё", "е").split())


class TaskService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user: User, task_id: int) -> Task | None:
        return await self.session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))

    async def list_open(self, user: User, limit: int = 50) -> list[Task]:
        res = await self.session.scalars(
            select(Task)
            .where(Task.user_id == user.id, Task.status.in_(OPEN_STATUSES))
            .order_by(Task.due_date.is_(None), Task.due_date, Task.deadline.is_(None), Task.deadline, Task.id)
            .limit(limit)
        )
        return list(res)

    async def find_duplicate(self, user: User, draft: TaskDraft) -> Task | None:
        """Открытая задача с тем же названием и тем же днём/дедлайном считается дублем."""
        tz = get_tz(user.timezone)
        key = normalize_title(draft.title)
        for task in await self.list_open(user, limit=500):
            if normalize_title(task.title) != key:
                continue
            same_day = task.due_date == draft.due_date
            same_deadline = (task.deadline.astimezone(tz).date() if task.deadline else None) == (
                draft.deadline.astimezone(tz).date() if draft.deadline else None
            )
            if same_day and same_deadline:
                return task
        return None

    async def create(self, user: User, draft: TaskDraft) -> tuple[Task, bool]:
        """Возвращает (задача, создана_ли). Повторное добавление не плодит дубли."""
        existing = await self.find_duplicate(user, draft)
        if existing is not None:
            return existing, False
        task = Task(
            user_id=user.id,
            title=draft.title,
            priority=draft.priority,
            due_date=draft.due_date,
            deadline=draft.deadline,
            estimated_minutes=draft.estimated_minutes,
            time_window=draft.time_window,
            status=TaskStatus.PENDING,
        )
        if draft.start is not None:
            minutes = draft.estimated_minutes or 60
            task.scheduled_start = draft.start
            task.scheduled_end = draft.start + timedelta(minutes=minutes)
            task.is_time_fixed = True
            task.due_date = draft.start.astimezone(get_tz(user.timezone)).date()
            task.status = TaskStatus.SCHEDULED
        self.session.add(task)
        await self.session.flush()
        log.info("task created id=%s user=%s", task.id, user.id)
        return task, True

    async def find_by_title(self, user: User, query: str) -> list[Task]:
        """Нечёткий поиск открытой задачи: подстрока, затем похожесть."""
        q = normalize_title(query)
        tasks = await self.list_open(user, limit=500)
        if not q:
            return []
        exact = [t for t in tasks if normalize_title(t.title) == q]
        if exact:
            return exact
        contains = [t for t in tasks if q in normalize_title(t.title) or normalize_title(t.title) in q]
        if contains:
            return contains
        # Совпадение по основам слов: «лабораторная» ≈ «лабораторную»
        words = [w for w in q.split() if w not in _STOP]
        scored = []
        for t in tasks:
            title_words = normalize_title(t.title).split()
            hits = sum(any(_same_stem(w, tw) for tw in title_words) for w in words)
            if hits:
                scored.append((hits, t))
        if scored:
            best = max(h for h, _ in scored)
            if best == len(words) or len(scored) == 1:
                return [t for h, t in scored if h == best]
        titles = {normalize_title(t.title): t for t in tasks}
        close = difflib.get_close_matches(q, list(titles), n=3, cutoff=0.6)
        return [titles[c] for c in close] or [t for _, t in scored]

    async def clear_pending_reminders(self, task: Task) -> None:
        await self.session.execute(delete(Reminder).where(Reminder.task_id == task.id, Reminder.sent_at.is_(None)))

    async def complete(self, task: Task) -> None:
        task.status = TaskStatus.DONE
        task.completed_at = now_utc()
        await self.clear_pending_reminders(task)

    async def cancel(self, task: Task) -> None:
        task.status = TaskStatus.CANCELLED
        await self.clear_pending_reminders(task)

    async def delete(self, task: Task) -> None:
        await self.session.delete(task)

    async def unschedule(self, task: Task) -> None:
        task.scheduled_start = None
        task.scheduled_end = None
        task.is_time_fixed = False
        task.status = TaskStatus.PENDING
        await self.clear_pending_reminders(task)


_STOP = {"по", "на", "в", "и", "с", "к", "до", "для", "задачу", "задача"}


def _same_stem(a: str, b: str) -> bool:
    """Грубое сравнение с учётом русских окончаний: общий префикс без последних 2 букв."""
    if a == b:
        return True
    k = max(4, min(len(a), len(b)) - 2)
    return len(a) >= 4 and len(b) >= 4 and a[:k] == b[:k]
