"""AI-слой: валидация JSON, обработка ошибок, fallback, rule-based разбор."""

from datetime import date, time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from planner_bot.db.models import Priority, TimeWindow
from planner_bot.schemas.ai import AIParseResult
from planner_bot.services.ai import MessageInterpreter
from planner_bot.services.ai.parser import AIParseError, AIParser, AIUnavailableError
from planner_bot.services.ai.rules import fast_intent, rule_parse
from planner_bot.services.ai.validation import to_command
from tests.conftest import NOW, TZ, TZ_NAME, local

VALID_JSON = """
{
  "intent": "add",
  "tasks": [
    {"title": "Закончить лабораторную по NLP", "due_date": null, "deadline": "2026-11-20",
     "priority": "high", "estimated_minutes": 120, "time_window": null, "start": null},
    {"title": "Зал", "due_date": "2026-11-19", "deadline": null,
     "priority": null, "estimated_minutes": 60, "time_window": "evening", "start": null}
  ],
  "events": [
    {"title": "Пара", "start": "2026-11-19T09:00", "end": "2026-11-19T12:00", "description": null},
    {"title": "Работа", "start": "2026-11-19T12:00", "end": "2026-11-19T18:00", "description": null}
  ],
  "target_title": null, "reschedule": null, "plan_date": null, "clarification_question": null
}
"""


# ------------------------------------------------------------- JSON → команда


def test_valid_ai_json_is_converted_to_command():
    raw = AIParseResult.model_validate_json(VALID_JSON)
    cmd = to_command(raw, TZ, NOW)
    assert cmd.intent == "add" and cmd.warnings == []
    lab, gym = cmd.tasks
    assert lab.priority == Priority.HIGH and lab.estimated_minutes == 120
    # Дедлайн только датой → конец дня в часовом поясе пользователя
    assert lab.deadline == local(2026, 11, 20, 23, 59)
    assert gym.time_window == TimeWindow.EVENING and gym.due_date == date(2026, 11, 19)
    assert gym.priority == Priority.MEDIUM  # null → значение по умолчанию
    para = cmd.events[0]
    assert para.start == local(2026, 11, 19, 9) and para.end == local(2026, 11, 19, 12)
    assert para.start.utcoffset().total_seconds() == 5 * 3600


def test_broken_json_fails_schema_validation():
    with pytest.raises(ValidationError):
        AIParseResult.model_validate_json('{"intent": "add", "tasks": [')
    with pytest.raises(ValidationError):
        AIParseResult.model_validate_json('{"intent": "fly_to_moon", "tasks": [], "events": []}')


def test_bad_items_are_dropped_with_warning_others_kept():
    raw = AIParseResult.model_validate_json(VALID_JSON)
    raw.events[0].end = "2026-11-19T08:00"  # конец раньше начала
    raw.tasks[0].deadline = "не дата"
    raw.tasks[1].estimated_minutes = 100000  # нелепая оценка → просто отбрасываем оценку
    cmd = to_command(raw, TZ, NOW)
    assert [e.title for e in cmd.events] == ["Работа"]
    assert [t.title for t in cmd.tasks] == ["Зал"]
    assert cmd.tasks[0].estimated_minutes is None
    assert len(cmd.warnings) == 2


def test_dates_far_in_past_or_future_rejected():
    raw = AIParseResult.model_validate_json(VALID_JSON)
    raw.events[0].start, raw.events[0].end = "2020-01-01T09:00", "2020-01-01T10:00"
    raw.tasks[0].deadline = "2031-01-01"
    cmd = to_command(raw, TZ, NOW)
    assert len(cmd.events) == 1 and len(cmd.tasks) == 1


def test_event_without_end_gets_default_duration():
    raw = AIParseResult.model_validate_json(VALID_JSON)
    raw.events[0].end = None
    cmd = to_command(raw, TZ, NOW)
    assert cmd.events[0].end == local(2026, 11, 19, 10)


def test_reschedule_spec_validated():
    raw = rule_parse("Перенеси лабораторную на завтра после 18", NOW)
    cmd = to_command(raw, TZ, NOW)
    assert cmd.intent == "reschedule"
    assert cmd.target_title == "лабораторную"
    assert cmd.reschedule.day == date(2026, 11, 19)
    assert cmd.reschedule.after_time == time(18)


# ------------------------------------------------------------- клиент Claude


def _fake_parser(behavior) -> AIParser:
    parser = AIParser(api_key="test-key")

    async def parse(**kwargs):
        if isinstance(behavior, Exception):
            raise behavior
        return behavior

    parser.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    return parser


def _response(parsed, stop_reason="end_turn"):
    return SimpleNamespace(
        parsed_output=parsed, stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=1, output_tokens=1)
    )


async def test_parser_returns_validated_result():
    raw = AIParseResult.model_validate_json(VALID_JSON)
    parser = _fake_parser(_response(raw))
    res = await parser.parse("…", now_local=NOW, tz_name=TZ_NAME, open_task_titles=[])
    assert res.intent == "add"


async def test_parser_invalid_json_raises_parse_error():
    try:
        AIParseResult.model_validate_json("{oops")
    except ValidationError as e:
        err = e
    parser = _fake_parser(err)
    with pytest.raises(AIParseError):
        await parser.parse("…", now_local=NOW, tz_name=TZ_NAME, open_task_titles=[])


async def test_parser_refusal_raises_parse_error():
    parser = _fake_parser(_response(None, stop_reason="refusal"))
    with pytest.raises(AIParseError):
        await parser.parse("…", now_local=NOW, tz_name=TZ_NAME, open_task_titles=[])


async def test_interpreter_falls_back_to_rules_when_ai_down():
    class DownParser:
        async def parse(self, *a, **kw):
            raise AIUnavailableError("connection")

    interp = MessageInterpreter(DownParser())
    res = await interp.interpret("Завтра купить продукты", now=NOW, tz_name=TZ_NAME, open_task_titles=[])
    assert res.source == "rules" and res.ai_failed
    assert res.command.tasks[0].title == "Купить продукты"
    assert res.command.tasks[0].due_date == date(2026, 11, 19)


async def test_interpreter_fast_path_skips_ai():
    class MustNotBeCalled:
        async def parse(self, *a, **kw):
            raise AssertionError("AI should not be called")

    interp = MessageInterpreter(MustNotBeCalled())
    res = await interp.interpret("Что у меня завтра?", now=NOW, tz_name=TZ_NAME, open_task_titles=[])
    assert res.source == "fast"
    assert res.command.intent == "show_plan" and res.command.plan_date == date(2026, 11, 19)


# ------------------------------------------------------------- rule-based


def test_rules_full_example():
    text = (
        "Завтра у меня пара с 9 до 12, потом работа до 18. Вечером хочу в зал. "
        "Ещё надо закончить лабораторную по NLP до пятницы"
    )
    cmd = to_command(rule_parse(text, NOW), TZ, NOW)
    assert cmd.intent == "add"
    assert [(e.title, e.start.hour, e.end.hour) for e in cmd.events] == [("Пара", 9, 12), ("Работа", 12, 18)]
    assert all(e.start.date() == date(2026, 11, 19) for e in cmd.events)
    gym, lab = cmd.tasks
    assert gym.title == "Зал" and gym.time_window == TimeWindow.EVENING
    assert lab.title == "Закончить лабораторную по NLP"
    # 18.11.2026 — среда, «до пятницы» → 20.11 23:59
    assert lab.deadline == local(2026, 11, 20, 23, 59)


def test_rules_deadline_today_time():
    cmd = to_command(rule_parse("Нужно сегодня до 22:00 закончить отчёт", NOW), TZ, NOW)
    task = cmd.tasks[0]
    assert task.title == "Закончить отчёт"
    assert task.deadline == local(2026, 11, 18, 22)
    assert task.priority == Priority.HIGH


def test_rules_simple_task_without_date():
    cmd = to_command(rule_parse("Мне нужно сделать лабораторную по NLP", NOW), TZ, NOW)
    t = cmd.tasks[0]
    assert t.title == "Сделать лабораторную по NLP"
    assert t.due_date is None and t.deadline is None


def test_rules_event_tomorrow():
    cmd = to_command(rule_parse("Завтра пара с 10 до 12", NOW), TZ, NOW)
    ev = cmd.events[0]
    assert (ev.title, ev.start, ev.end) == ("Пара", local(2026, 11, 19, 10), local(2026, 11, 19, 12))


@pytest.mark.parametrize(
    "text,intent",
    [
        ("Покажи мои задачи", "show_tasks"),
        ("📝 Задачи", "show_tasks"),
        ("Что я должен сделать сегодня?", "show_plan"),
        ("Составь мне план на сегодня", "show_plan"),
        ("📅 Сегодня", "show_plan"),
        ("📊 Статистика", "stats"),
    ],
)
def test_fast_intents(text, intent):
    assert fast_intent(text, NOW.date()).intent == intent


def test_fast_path_does_not_steal_additions():
    assert fast_intent("Завтра у меня пара с 9 до 12", NOW.date()) is None
    assert fast_intent("Добавь задачу завтра купить продукты", NOW.date()) is None


def test_rules_delete_and_complete():
    assert rule_parse("Удали задачу купить продукты", NOW).target_title == "купить продукты"
    r = rule_parse("Сделал отчёт", NOW)
    assert r.intent == "complete" and r.target_title == "отчёт"
