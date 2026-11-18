"""Сквозные сценарии без Telegram: текст → разбор → бизнес-логика → БД → ответ."""

from datetime import date, time, timedelta

from sqlalchemy import select

from planner_bot.bot import scheduler as sched
from planner_bot.bot.actions import Actions
from planner_bot.db.models import Reminder, Task, TaskStatus
from planner_bot.services.ai import MessageInterpreter
from planner_bot.services.ai.time_input import parse_time_answer
from planner_bot.services.settings import apply_settings_text
from planner_bot.services.tasks import TaskService
from tests.conftest import NOW, TZ_NAME, local

interp = MessageInterpreter(None)  # без ключа — rule-based


async def say(session, user, text, now=NOW):
    titles = [t.title for t in await TaskService(session).list_open(user)]
    res = await interp.interpret(text, now=now, tz_name=TZ_NAME, open_task_titles=titles)
    return await Actions(session, user, now).execute(res.command)


async def test_full_day_flow(session, user):
    r = await say(session, user, "Сегодня пара с 9 до 12, потом работа до 18. Вечером хочу в зал")
    assert "Пара" in r.text and "Работа" in r.text and "Зал" in r.text

    r = await say(session, user, "Нужно сегодня до 22:00 закончить отчёт")
    assert "Закончить отчёт" in r.text

    plan = await say(session, user, "Что мне сегодня делать?")
    assert "План на сегодня" in plan.text
    # Отчёт горит сегодня → встаёт в первое свободное окно (до пары, 08:00)
    assert "08:00–09:00</b>\n📝 Закончить отчёт" in plan.text
    assert "12:00–18:00</b>\n💻 Работа" in plan.text
    assert "18:00–19:00" in plan.text  # зал вечером


async def test_add_is_idempotent(session, user):
    await say(session, user, "Завтра купить продукты")
    r = await say(session, user, "Завтра купить продукты")
    assert "Уже есть" in r.text
    assert len(await TaskService(session).list_open(user)) == 1


async def test_reschedule_delete_complete_by_text(session, user):
    await say(session, user, "Мне нужно сделать лабораторную по NLP")
    await say(session, user, "Завтра купить продукты")

    r = await say(session, user, "Перенеси лабораторную на пятницу")
    assert "Перенёс" in r.text
    lab = (await TaskService(session).find_by_title(user, "лабораторная"))[0]
    assert lab.due_date == date(2026, 11, 20)

    r = await say(session, user, "Удали задачу купить продукты")
    assert "Удалил" in r.text

    r = await say(session, user, "Сделал лабораторную")
    assert "выполнена" in r.text
    assert lab.status == TaskStatus.DONE


async def test_reschedule_without_title_uses_context(session, user):
    await say(session, user, "Сделать отчёт")
    r = await say(session, user, "Перенеси на завтра после 18")
    assert "после 18:00" in r.text


async def test_fixed_task_conflicting_with_event_is_unfixed(session, user):
    await say(session, user, "Сегодня пара с 9 до 12")
    r = await say(session, user, "Сегодня в 10:00 позвонить маме")
    assert "пересекается" in r.text
    task = (await TaskService(session).find_by_title(user, "позвонить маме"))[0]
    assert not task.is_time_fixed


def test_time_answer_parsing():
    today = NOW.date()
    assert parse_time_answer("15:30", today).exact_time == time(15, 30)
    s = parse_time_answer("завтра в 10", today)
    assert s.day == today + timedelta(days=1) and s.exact_time == time(10)
    s = parse_time_answer("в пятницу после 18", today)
    assert s.day == date(2026, 11, 20) and s.after_time == time(18)
    assert parse_time_answer("купить хлеб", today) is None


def test_settings_by_text(user):
    assert "Europe/Moscow" in apply_settings_text(user, "часовой пояс Europe/Moscow")
    assert user.timezone == "Europe/Moscow"
    assert "Не знаю" in apply_settings_text(user, "часовой пояс Mars/Olympus")
    apply_settings_text(user, "утренний план в 7:30")
    assert user.morning_time == time(7, 30)
    apply_settings_text(user, "напоминать за 10 минут")
    assert user.reminder_minutes == 10
    assert apply_settings_text(user, "купить хлеб") is None


# ------------------------------------------------------------ фоновые задачи


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


class FactoryFromSession:
    """async_sessionmaker-подобная обёртка над тестовой сессией."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        session = self.session

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_reminders_are_sent_once(session, user):
    await say(session, user, "Сегодня пара с 8 до 12")
    t, _ = await TaskService(session).create(user, _draft("Лабораторная", 60))
    from planner_bot.services.planner import PlannerService

    await PlannerService(session).plan_day(user, NOW.date(), NOW)
    await session.commit()
    assert t.scheduled_start == local(2026, 11, 18, 12)

    bot, factory = FakeBot(), FactoryFromSession(session)
    at = local(2026, 11, 18, 11, 46)
    await sched.process_reminders(bot, factory, at)
    texts = [s[1] for s in bot.sent]
    assert any("Через 14 мин" in x and "Лабораторная" in x for x in texts)
    n = len(bot.sent)
    await sched.process_reminders(bot, factory, at)
    assert len(bot.sent) == n  # повторно не шлём

    await sched.process_reminders(bot, factory, local(2026, 11, 18, 13, 1))
    assert "Как прошло?" in bot.sent[-1][1]
    unsent = list(await session.scalars(select(Reminder).where(Reminder.sent_at.is_(None))))
    assert unsent == []


async def test_morning_and_evening_digests(session, user):
    await say(session, user, "Сегодня пара с 9 до 12")
    await say(session, user, "Сделать отчёт")
    bot, factory = FakeBot(), FactoryFromSession(session)

    await sched.process_digests(bot, factory, local(2026, 11, 18, 8, 1))
    assert "Доброе утро" in bot.sent[-1][1] and "Главная задача дня" in bot.sent[-1][1]
    n = len(bot.sent)
    await sched.process_digests(bot, factory, local(2026, 11, 18, 8, 2))
    assert len(bot.sent) == n

    await sched.process_digests(bot, factory, local(2026, 11, 18, 21, 31))
    summary_text, markup = bot.sent[-1][1], bot.sent[-1][2]
    assert "Итоги дня" in summary_text and "❌ Сделать отчёт" in summary_text
    assert markup is not None  # кнопки «перенести на завтра?»


async def test_stale_reminder_after_downtime_skipped(session, user):
    t, _ = await TaskService(session).create(user, _draft("Отчёт", 30))
    session.add(Reminder(user_id=user.id, task_id=t.id, kind="before_start", fire_at=local(2026, 11, 18, 6)))
    t.scheduled_start, t.scheduled_end = local(2026, 11, 18, 6, 15), local(2026, 11, 18, 6, 45)
    await session.commit()
    bot = FakeBot()
    await sched.process_reminders(bot, FactoryFromSession(session), local(2026, 11, 18, 9))
    assert bot.sent == []


def _draft(title, minutes):
    from planner_bot.schemas.commands import TaskDraft

    return TaskDraft(title=title, estimated_minutes=minutes)


async def test_task_model_roundtrip_keeps_utc(session, user):
    t, _ = await TaskService(session).create(user, _draft("X", 30))
    t.deadline = local(2026, 11, 20, 23, 59)
    task_id = t.id
    await session.commit()
    session.expire_all()
    loaded = await session.scalar(select(Task).where(Task.id == task_id))
    assert loaded.deadline == local(2026, 11, 20, 23, 59)
    assert loaded.deadline.utcoffset() == timedelta(0)
