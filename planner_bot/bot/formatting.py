"""Тексты сообщений (HTML parse mode). Пользовательский текст всегда экранируется."""

import re
from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from planner_bot.db.models import Priority, Task, User
from planner_bot.services.analytics import DaySummary, Stats
from planner_bot.services.scheduling import DayPlan, PlanItem
from planner_bot.utils.dates import human_date

EMOJI_RULES = [
    (r"пар[аыу]?\b|лекци|семинар|учеб|универ|занят", "🎓"),
    (r"работ|офис|смен", "💻"),
    (r"зал|трениров|спорт|бег|фитнес|йог|плаван", "🏋️"),
    (r"обед|ужин|завтрак|поесть", "🍴"),
    (r"лаб|отч[её]т|доклад|курсов|диплом|реферат|домашк|дз\b", "📝"),
    (r"купить|продукт|магазин|покупк", "🛒"),
    (r"позвон|звон|созвон", "📞"),
    (r"встреч|собеседов", "🤝"),
    (r"врач|стоматолог|больниц|аптек", "🩺"),
    (r"чита|книг", "📖"),
    (r"уборк|убрать|стирк", "🧹"),
    (r"сообщени|почт|письм|ответить", "✉️"),
]


def emoji_for(title: str, kind: str = "task") -> str:
    low = title.lower()
    for pattern, emoji in EMOJI_RULES:
        if re.search(pattern, low):
            return emoji
    return {"event": "📌", "break": "🍴"}.get(kind, "📝")


def hm(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def span(item: PlanItem, tz: ZoneInfo) -> str:
    return f"{hm(item.start, tz)}–{hm(item.end, tz)}"


def duration(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} ч {m} мин"
    return f"{h} ч" if h else f"{m} мин"


def _item_title(item: PlanItem) -> str:
    mark = " ⚠️" if item.overdue else ""
    return f"{emoji_for(item.title, item.kind)} {escape(item.title)}{mark}"


def format_plan(plan: DayPlan, tz: ZoneInfo, today: date) -> str:
    title = f"📅 План на {human_date(plan.day, today)}"
    if not plan.items and not plan.unscheduled:
        return f"{title}\n\nПока пусто. Напиши, что планируешь, — например:\n«Завтра пара с 9 до 12, вечером зал»"
    lines = [f"<b>{title}</b>", ""]
    for item in plan.items:
        lines += [f"<b>{span(item, tz)}</b>", _item_title(item), ""]
    if plan.unscheduled:
        lines.append("⚠️ <b>Не поместилось в этот день:</b>")
        lines += [f"• {escape(t.title)}" for t in plan.unscheduled]
    return "\n".join(lines).strip()


def format_morning(plan: DayPlan, tz: ZoneInfo, user: User) -> str:
    name = f", {escape(user.first_name)}" if user.first_name else ""
    lines = [f"☀️ <b>Доброе утро{name}!</b>", ""]
    if not plan.items and not plan.unscheduled:
        lines.append("На сегодня ничего не запланировано. Напиши, что хочешь сделать 🙂")
        return "\n".join(lines)
    lines.append("Твой план на сегодня:\n")
    lines += [f"{span(i, tz)} {_item_title(i)}" for i in plan.items]
    if plan.unscheduled:
        lines.append("\n⚠️ Не поместилось: " + ", ".join(escape(t.title) for t in plan.unscheduled))
    if plan.main_task:
        lines += ["", "<b>Главная задача дня:</b>", f"{emoji_for(plan.main_task.title)} {escape(plan.main_task.title)}"]
    lines += ["", f"Сегодня у тебя задач: {plan.task_count}."]
    return "\n".join(lines)


PRIORITY_MARK = {Priority.HIGH: "🔴", Priority.MEDIUM: "🟡", Priority.LOW: "🟢"}


def task_line(task: Task, tz: ZoneInfo, today: date) -> str:
    parts = [f"{PRIORITY_MARK.get(Priority(task.priority), '')} {escape(task.title)}"]
    meta = []
    if task.scheduled_start and task.scheduled_end:
        d = task.scheduled_start.astimezone(tz).date()
        meta.append(f"{human_date(d, today)} {hm(task.scheduled_start, tz)}–{hm(task.scheduled_end, tz)}")
    elif task.due_date:
        meta.append(human_date(task.due_date, today))
    if task.deadline:
        dl = task.deadline.astimezone(tz)
        when = human_date(dl.date(), today)
        meta.append(f"до {when}" + (f" {dl:%H:%M}" if dl.strftime("%H:%M") != "23:59" else ""))
    if task.estimated_minutes:
        meta.append(f"~{duration(task.estimated_minutes)}")
    if meta:
        parts.append(f"<i>({', '.join(meta)})</i>")
    return " ".join(parts)


def format_tasks(tasks: list[Task], tz: ZoneInfo, today: date) -> str:
    if not tasks:
        return "📝 Открытых задач нет. Напиши, например: «Завтра купить продукты»"
    lines = ["<b>📝 Твои задачи</b>", ""]
    lines += [f"{n}. {task_line(t, tz, today)}" for n, t in enumerate(tasks, 1)]
    lines += ["", "Отметить выполненной — кнопкой ниже или текстом «сделал …»."]
    return "\n".join(lines)


def format_summary(summary: DaySummary, tz: ZoneInfo) -> str:
    lines = ["🌙 <b>Итоги дня</b>", ""]
    if summary.total:
        lines += [f"Выполнено: {len(summary.done)}/{summary.total} задач", ""]
    lines += [f"✅ {escape(t.title)}" for t in summary.done]
    lines += [f"❌ {escape(t.title)}" for t in summary.not_done]
    if summary.events:
        lines += ["", "События дня:"]
        lines += [
            f"📌 {hm(e.start_time, tz)}–{hm(e.end_time, tz)} {escape(e.title)}" for e in summary.events
        ]
    if summary.not_done:
        lines += ["", "Незавершённые задачи перенести на завтра?"]
    elif summary.total:
        lines += ["", "Все задачи выполнены — отличный день! 🎉"]
    return "\n".join(lines)


def format_stats(stats: Stats) -> str:
    rate = f" ({round(stats.done_today * 100 / stats.planned_today)}%)" if stats.planned_today else ""
    return (
        "<b>📊 Статистика</b>\n\n"
        f"Сегодня выполнено: {stats.done_today}/{stats.planned_today}{rate}\n"
        f"За 7 дней выполнено: {stats.done_week}\n"
        f"Открытых задач: {stats.open_total}\n"
        f"Просрочено: {stats.overdue}"
    )


def format_settings(user: User) -> str:
    return (
        "<b>⚙️ Настройки</b>\n\n"
        f"🌍 Часовой пояс: <code>{escape(user.timezone)}</code>\n"
        f"☀️ Утренний план: {user.morning_time:%H:%M}\n"
        f"🌙 Итоги дня: {user.evening_time:%H:%M}\n"
        f"🕗 Активное время: {user.day_start:%H:%M}–{user.day_end:%H:%M}\n"
        f"⏰ Напоминать за: {user.reminder_minutes} мин\n"
        f"🔔 Уведомления: {'вкл' if user.notifications_enabled else 'выкл'}\n\n"
        "Изменить можно обычным текстом:\n"
        "• «часовой пояс Europe/Moscow»\n"
        "• «утренний план в 7:30»\n"
        "• «итоги в 22:00»\n"
        "• «день с 7 до 23»\n"
        "• «напоминать за 10 минут»\n"
        "• «уведомления выкл»"
    )


HELP_TEXT = (
    "Я — твой AI-планировщик 🗓\n\n"
    "Просто пиши обычным текстом, например:\n"
    "• «Завтра у меня пара с 9 до 12, потом работа до 18. Вечером хочу в зал»\n"
    "• «Нужно сегодня до 22:00 закончить отчёт»\n"
    "• «Что мне сегодня делать?» / «Что у меня завтра?»\n"
    "• «Покажи мои задачи»\n"
    "• «Перенеси лабораторную на пятницу»\n"
    "• «Удали задачу купить продукты»\n"
    "• «Сделал отчёт»\n\n"
    "Я составлю план дня, напомню о делах и вечером подведу итоги."
)
