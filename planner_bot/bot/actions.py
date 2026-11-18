"""Исполнение распознанных команд: вызывает сервисы и готовит ответ пользователю.

Здесь нет разбора текста — только бизнес-логика по уже провалидированной команде.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from html import escape

from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.bot import formatting as fmt
from planner_bot.bot import keyboards as kb
from planner_bot.db.models import Task, User
from planner_bot.schemas.commands import ParsedCommand, RescheduleSpec
from planner_bot.services.analytics import AnalyticsService
from planner_bot.services.events import EventService
from planner_bot.services.planner import PlannerService, RescheduleError, RescheduleResult
from planner_bot.services.scheduling import Interval, conflicts
from planner_bot.services.tasks import TaskService
from planner_bot.utils.dates import get_tz, human_date

log = logging.getLogger(__name__)


@dataclass
class Reply:
    text: str
    markup: InlineKeyboardMarkup | None = None
    extra: list[str] = field(default_factory=list)


class Actions:
    def __init__(self, session: AsyncSession, user: User, now: datetime):
        self.session = session
        self.user = user
        self.now = now
        self.tz = get_tz(user.timezone)
        self.today = now.astimezone(self.tz).date()
        self.tasks = TaskService(session)
        self.events = EventService(session)
        self.planner = PlannerService(session)

    # ------------------------------------------------------------ dispatch

    async def execute(self, cmd: ParsedCommand) -> Reply:
        handler = {
            "add": self.add,
            "show_plan": self.show_plan,
            "show_tasks": self.show_tasks,
            "reschedule": self.reschedule,
            "complete": self.complete,
            "delete": self.delete,
            "stats": self.stats,
            "help": self.help,
            "smalltalk": self.smalltalk,
        }.get(cmd.intent, self.unknown)
        reply = await handler(cmd)
        if cmd.warnings:
            reply.text += "\n\n" + "\n".join(f"⚠️ {escape(w)}" for w in cmd.warnings)
        return reply

    # ------------------------------------------------------------ intents

    async def add(self, cmd: ParsedCommand) -> Reply:
        if not cmd.tasks and not cmd.events:
            question = cmd.clarification_question or "Не понял, что добавить. Опиши задачу чуть подробнее?"
            return Reply(escape(question))

        lines: list[str] = []
        touched_days = set()

        for draft in cmd.events:
            event, created, overlaps = await self.events.create(self.user, draft)
            d = event.start_time.astimezone(self.tz).date()
            touched_days.add(d)
            when = f"{human_date(d, self.today)} {fmt.hm(event.start_time, self.tz)}–{fmt.hm(event.end_time, self.tz)}"
            prefix = "📌" if created else "↩️ Уже есть:"
            lines.append(f"{prefix} {fmt.emoji_for(event.title, 'event')} <b>{escape(event.title)}</b> — {when}")
            if overlaps:
                lines.append("   ⚠️ пересекается с: " + ", ".join(escape(o.title) for o in overlaps))

        for draft in cmd.tasks:
            task, created = await self.tasks.create(self.user, draft)
            if created and task.is_time_fixed:
                await self._fix_task_conflicts(task, lines)
            if task.due_date:
                touched_days.add(task.due_date)
            elif task.deadline is None or task.deadline.astimezone(self.tz).date() >= self.today:
                touched_days.add(self.today)
            prefix = "📝" if created else "↩️ Уже есть:"
            lines.append(f"{prefix} {fmt.task_line(task, self.tz, self.today)}")
            if created:
                self.user.context_task_id = task.id

        # Если что-то добавлено на сегодня — сразу пересобираем план (и напоминания)
        if self.today in touched_days:
            await self.planner.plan_day(self.user, self.today, self.now)

        hint_day = min(touched_days) if touched_days else self.today
        hint = "сегодня" if hint_day == self.today else human_date(hint_day, self.today)
        text = "✅ <b>Записал:</b>\n" + "\n".join(lines)
        text += f"\n\nНапиши «что у меня {hint}?», чтобы увидеть план."
        if cmd.clarification_question:
            text += f"\n\n❓ {escape(cmd.clarification_question)}"
        return Reply(text)

    async def _fix_task_conflicts(self, task: Task, lines: list[str]) -> None:
        """Задача с точным временем не может стоять поверх события — снимаем закрепление."""
        day = task.scheduled_start.astimezone(self.tz).date()
        busy, _ = await self.planner.busy_blocks(self.user, day, self.now, exclude_task=task.id)
        clash = conflicts(Interval(task.scheduled_start, task.scheduled_end), busy)
        if clash:
            await self.tasks.unschedule(task)
            task.due_date = day
            names = ", ".join(escape(b.title) for b in clash)
            lines.append(f"⚠️ «{escape(task.title)}» пересекается с: {names} — поставлю в свободное время.")

    async def show_plan(self, cmd: ParsedCommand) -> Reply:
        day = cmd.plan_date or self.today
        if day < self.today:
            summary = await AnalyticsService(self.session).day_summary(self.user, day)
            return Reply(fmt.format_summary(summary, self.tz))
        plan = await self.planner.plan_day(self.user, day, self.now)
        return Reply(fmt.format_plan(plan, self.tz, self.today))

    async def show_tasks(self, cmd: ParsedCommand | None = None) -> Reply:
        tasks = await self.tasks.list_open(self.user, limit=30)
        return Reply(fmt.format_tasks(tasks, self.tz, self.today), kb.tasks_kb(tasks))

    async def _resolve(self, cmd: ParsedCommand, action: str) -> tuple[Task | None, Reply | None]:
        """Находит задачу по названию или по контексту. Если вариантов несколько — спрашиваем."""
        if cmd.target_title:
            found = await self.tasks.find_by_title(self.user, cmd.target_title)
            if not found:
                return None, Reply(f"Не нашёл задачу «{escape(cmd.target_title)}». Посмотреть список: «покажи задачи».")
            if len(found) > 1:
                return None, Reply("Какую именно задачу?", kb.choose_task_kb(found, action))
            return found[0], None
        if self.user.context_task_id:
            task = await self.tasks.get(self.user, self.user.context_task_id)
            if task is not None and task.status in ("pending", "scheduled"):
                return task, None
        open_tasks = await self.tasks.list_open(self.user, limit=8)
        if not open_tasks:
            return None, Reply("У тебя нет открытых задач.")
        return None, Reply("О какой задаче речь?", kb.choose_task_kb(open_tasks, action))

    async def reschedule(self, cmd: ParsedCommand) -> Reply:
        task, reply = await self._resolve(cmd, "postpone")
        if reply:
            return reply
        self.user.context_task_id = task.id
        spec = cmd.reschedule
        if spec is None or (spec.day is None and spec.after_time is None and spec.exact_time is None):
            return Reply(f"Куда перенести «{escape(task.title)}»?", kb.postpone_kb(task.id))
        return await self.apply_reschedule(task, spec)

    async def apply_reschedule(self, task: Task, spec: RescheduleSpec) -> Reply:
        try:
            res = await self.planner.reschedule(self.user, task, spec, self.now)
        except RescheduleError as e:
            return Reply(f"⚠️ {escape(str(e))}")
        return Reply(self._reschedule_text(res))

    def _reschedule_text(self, res: RescheduleResult) -> str:
        day = human_date(res.day, self.today)
        title = escape(res.task.title)
        if res.start and res.end and (res.day == self.today or res.task.is_time_fixed):
            text = f"🕐 Перенёс «{title}» на {day}, {fmt.hm(res.start, self.tz)}–{fmt.hm(res.end, self.tz)}"
            if res.adjusted:
                text += "\n(сдвинул, чтобы не пересекаться с другими делами)"
            return text
        if res.task.not_before:
            return f"🕐 Перенёс «{title}» на {day}, после {fmt.hm(res.task.not_before, self.tz)}"
        if res.start is None and res.day == self.today:
            return f"🕐 Перенёс «{title}» на {day}, но свободного окна не нашлось — покажу в плане отдельно."
        return f"🕐 Перенёс «{title}» на {day}. Время подберу в утреннем плане."

    async def reschedule_evening(self, task: Task) -> Reply:
        local_now = self.now.astimezone(self.tz).time()
        after = time(18, 0) if local_now < time(18, 0) else None
        if local_now >= self.user.day_end:
            return Reply("Сегодня уже поздно 🌙 Перенести на завтра?", kb.postpone_kb(task.id))
        return await self.apply_reschedule(task, RescheduleSpec(day=self.today, after_time=after))

    async def reschedule_tomorrow(self, task: Task) -> Reply:
        return await self.apply_reschedule(task, RescheduleSpec(day=self.today + timedelta(days=1)))

    async def complete(self, cmd: ParsedCommand) -> Reply:
        task, reply = await self._resolve(cmd, "done")
        if reply:
            return reply
        return await self.complete_task(task)

    async def complete_task(self, task: Task) -> Reply:
        await self.tasks.complete(task)
        return Reply(f"✅ Отлично! «{escape(task.title)}» выполнена.")

    async def delete(self, cmd: ParsedCommand) -> Reply:
        task, reply = await self._resolve(cmd, "del")
        if reply:
            return reply
        return await self.delete_task(task)

    async def delete_task(self, task: Task) -> Reply:
        title = task.title
        if self.user.context_task_id == task.id:
            self.user.context_task_id = None
        await self.tasks.delete(task)
        return Reply(f"🗑 Удалил задачу «{escape(title)}».")

    async def stats(self, cmd: ParsedCommand | None = None) -> Reply:
        stats = await AnalyticsService(self.session).stats(self.user, self.now)
        return Reply(fmt.format_stats(stats))

    async def help(self, cmd: ParsedCommand | None = None) -> Reply:
        return Reply(fmt.HELP_TEXT)

    async def smalltalk(self, cmd: ParsedCommand) -> Reply:
        return Reply("Привет! 👋 Расскажи, что у тебя запланировано, или спроси «что мне сегодня делать?»")

    async def unknown(self, cmd: ParsedCommand) -> Reply:
        if cmd.clarification_question:
            return Reply(escape(cmd.clarification_question))
        return Reply(
            "Я не совсем понял 🤔 Я помогаю с планами и задачами. Попробуй, например:\n"
            "«Завтра пара с 10 до 12» или «Что мне сегодня делать?»"
        )
