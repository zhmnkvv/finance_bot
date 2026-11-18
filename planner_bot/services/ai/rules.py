"""Rule-based разбор без LLM.

1. fast_intent — частые простые запросы («Покажи мои задачи», «Что у меня завтра?»).
   Их надёжнее и дешевле понять регуляркой, чем вызывать модель.
2. rule_parse — запасной разбор, когда AI недоступен (нет ключа, сеть, лимиты).
   Понимает типовые фразы: даты «сегодня/завтра/в пятницу», интервалы «с 9 до 12»,
   дедлайны «до 22:00», «до пятницы», «вечером».
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from planner_bot.schemas.ai import AIEvent, AIParseResult, AIReschedule, AITask, empty_result
from planner_bot.utils.dates import WEEKDAYS_RU, next_weekday, parse_hhmm

T = r"(\d{1,2}(?:[:.]\d{2})?)"
_WD = "|".join(sorted(WEEKDAYS_RU, key=len, reverse=True))

RE_DAY = re.compile(rf"\b(сегодня|завтра|послезавтра|(?:в|во|на)\s+({_WD}))\b", re.I)
RE_DEADLINE_DAY = re.compile(rf"\bдо\s+({_WD}|завтра|послезавтра)\b", re.I)
RE_DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")
RE_RANGE = re.compile(rf"\bс\s+{T}\s+до\s+{T}\b|\b(\d{{1,2}}:\d{{2}})\s*[-–—]\s*(\d{{1,2}}:\d{{2}})\b", re.I)
RE_UNTIL_TIME = re.compile(rf"\bдо\s+{T}(?:\s*час\w*)?\b", re.I)
RE_AT_TIME = re.compile(rf"\b(?:в|на)\s+{T}\b(?!\s*(?:мин|час))", re.I)
RE_AFTER_TIME = re.compile(rf"\bпосле\s+{T}\b", re.I)
RE_DURATION = re.compile(r"\b(\d{1,3})\s*(мин\w*|час\w*|ч)\b", re.I)
RE_WINDOW = {
    "morning": re.compile(r"\b(утром|с утра)\b", re.I),
    "afternoon": re.compile(r"\b(днём|днем|после обеда)\b", re.I),
    "evening": re.compile(r"\b(вечером|вечер)\b", re.I),
}
RE_SPLIT = re.compile(r"[.;!\n]+|,?\s+(?:а\s+)?потом\s+|,?\s+(?:а\s+)?ещё\s+|,?\s+(?:а\s+)?еще\s+|,\s+(?=затем)", re.I)
FILLERS = re.compile(
    r"\b(мне|у меня|нужно|надо|хочу|хотелось бы|необходимо|добавь|добавить|задачу|задача|"
    r"напомни|пожалуйста|ещё|еще|потом|затем|тоже|также|а|сегодня|завтра|послезавтра|"
    r"утром|с утра|днём|днем|вечером|срочно|важно)\b",
    re.IGNORECASE,
)
URGENT = re.compile(r"\b(срочно|важно|обязательно|горит)\b", re.I)

EVENT_WORDS = re.compile(r"\b(пар[аыу]|лекци\w*|семинар\w*|работ[аеу]|встреч\w*|созвон\w*|урок\w*|приём|прием|экзамен\w*)\b", re.I)


def _day_from_word(word: str, wd: str | None, today: date) -> date:
    if word == "сегодня":
        return today
    if word == "завтра":
        return today + timedelta(days=1)
    if word == "послезавтра":
        return today + timedelta(days=2)
    return next_weekday(today, WEEKDAYS_RU[wd], allow_today=False)


def find_day(text: str, today: date) -> date | None:
    m = RE_DAY.search(text)
    if m:
        return _day_from_word(m.group(1).split()[0].lower(), (m.group(2) or "").lower() or None, today)
    m = RE_DATE_NUM.search(text)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        try:
            res = date(y, mo, d)
        except ValueError:
            return None
        return res if m.group(3) or res >= today else date(y + 1, mo, d)
    return None


def find_deadline_day(text: str, today: date) -> date | None:
    m = RE_DEADLINE_DAY.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    if word in WEEKDAYS_RU:
        return next_weekday(today, WEEKDAYS_RU[word], allow_today=False)
    return _day_from_word(word, None, today)


def _iso(d: date, t: time) -> str:
    return datetime.combine(d, t).strftime("%Y-%m-%dT%H:%M")


def _clean_title(text: str, *patterns: re.Pattern) -> str:
    for p in (RE_RANGE, RE_UNTIL_TIME, RE_AT_TIME, RE_AFTER_TIME, RE_DAY, RE_DEADLINE_DAY, RE_DATE_NUM, RE_DURATION,
              *patterns):  # fmt: skip
        text = p.sub(" ", text)
    text = FILLERS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.-—:")
    # «хочу в зал» → «Зал»
    text = re.sub(r"^(в|во|на|к|ко|по)\s+", "", text, flags=re.I)
    return text[:1].upper() + text[1:] if text else ""


# ---------------------------------------------------------------- fast path

_PLAN_RE = re.compile(
    r"^(что\s+(мне|я|у\s+меня)\b.*|.*\bплан\b.*|.*\bрасписани\w*.*|📅.*|сегодня\??|завтра\??)$"
)
_TASKS_RE = re.compile(r"^(📝.*|(покажи|список|какие|мои|все)\b.*задач\w*.*|задачи\??)$")


def fast_intent(text: str, today: date) -> AIParseResult | None:
    t = text.strip().lower().rstrip("!. ")
    if t in {"/help", "помощь", "помоги", "что ты умеешь?", "что ты умеешь", "help"}:
        return empty_result("help")
    if t.startswith("📊") or "статистик" in t:
        return empty_result("stats")
    if _TASKS_RE.match(t):
        return empty_result("show_tasks")
    if _PLAN_RE.match(t) and not RE_RANGE.search(t) and len(t) < 80:
        # «Завтра у меня пара с 9 до 12» — это не запрос плана, а добавление
        if re.search(r"\b(добавь|надо|нужно|хочу)\b", t):
            return None
        day = find_day(t, today) or today
        return empty_result("show_plan", plan_date=day.isoformat())
    return None


# ---------------------------------------------------------------- fallback


def _parse_reschedule(text: str, today: date) -> AIParseResult:
    body = re.sub(r"^\s*перенеси(те)?\s*", "", text, flags=re.I)
    m = re.search(r"\s+на\s+", body)
    target_part, when = (body[: m.start()], body[m.end() :]) if m else ("", body)
    if target_part.strip().startswith("задач"):
        target_part = target_part.split(maxsplit=1)[1] if " " in target_part.strip() else ""
    when_full = "на " + when
    day = find_day(when_full, today)
    after = RE_AFTER_TIME.search(when_full)
    exact = RE_AT_TIME.search(when_full) if not after else None
    exact_t = parse_hhmm(exact.group(1)) if exact else None
    return empty_result(
        "reschedule",
        target_title=target_part.strip(" ,.") or None,
        reschedule=AIReschedule(
            date=day.isoformat() if day else None,
            after_time=after.group(1).replace(".", ":") if after else None,
            exact_time=exact_t.strftime("%H:%M") if exact_t else None,
        ),
    )


def rule_parse(text: str, now_local: datetime) -> AIParseResult:
    today = now_local.date()
    low = text.strip().lower()

    fast = fast_intent(text, today)
    if fast is not None:
        return fast
    if re.match(r"^\s*(удали|убери|удалить)\b", low):
        target = re.sub(r"^\s*(удали|убери|удалить)\s+(задачу\s+)?", "", low).strip(" .")
        return empty_result("delete", target_title=target or None)
    if re.match(r"^\s*перенеси", low):
        return _parse_reschedule(low, today)
    m = re.match(r"^\s*(я\s+)?(сделал|сделала|выполнил|выполнила|закончил|закончила|готово)\b[:\s]*(.*)$", low)
    if m:
        return empty_result("complete", target_title=m.group(3).strip(" .") or None)
    if low in {"привет", "спасибо", "ок", "окей", "хорошо", "/start"}:
        return empty_result("smalltalk")

    tasks: list[AITask] = []
    events: list[AIEvent] = []
    ctx_day: date | None = None
    prev_end: tuple[date, time] | None = None

    for clause in filter(None, (c.strip() for c in RE_SPLIT.split(text.strip()))):
        day = find_day(clause, today) or ctx_day
        ctx_day = day or ctx_day
        rng = RE_RANGE.search(clause)
        until = RE_UNTIL_TIME.search(clause)
        is_event_word = bool(EVENT_WORDS.search(clause))

        if rng:
            a, b = (rng.group(1), rng.group(2)) if rng.group(1) else (rng.group(3), rng.group(4))
            ta, tb = parse_hhmm(a), parse_hhmm(b)
            if ta and tb:
                d = day or today
                events.append(AIEvent(title=_clean_title(clause), start=_iso(d, ta), end=_iso(d, tb), description=None))
                prev_end = (d, tb)
                continue
        if until and prev_end and is_event_word and not RE_DEADLINE_DAY.search(clause):
            # «…потом работа до 18» — событие от конца предыдущего до указанного времени
            tb = parse_hhmm(until.group(1))
            d, ta = prev_end
            if tb and tb > ta:
                events.append(AIEvent(title=_clean_title(clause), start=_iso(d, ta), end=_iso(d, tb), description=None))
                prev_end = (d, tb)
                continue

        title = _clean_title(clause)
        if not title:
            continue
        deadline = None
        dl_day = find_deadline_day(clause, today)
        if dl_day:
            deadline = dl_day.isoformat()
        elif until:
            tt = parse_hhmm(until.group(1))
            if tt:
                deadline = _iso(day or today, tt)
        at = RE_AT_TIME.search(clause)
        start = None
        if at and (t := parse_hhmm(at.group(1))):
            start = _iso(day or today, t)
        dur = RE_DURATION.search(clause)
        minutes = None
        if dur:
            n = int(dur.group(1))
            minutes = n * 60 if dur.group(2).startswith("ч") else n
        window = next((k for k, p in RE_WINDOW.items() if p.search(clause)), None)
        urgent = bool(URGENT.search(clause)) or (deadline is not None and deadline[:10] <= (today + timedelta(days=1)).isoformat())
        tasks.append(
            AITask(
                title=title,
                due_date=day.isoformat() if day and not deadline else None,
                deadline=deadline,
                priority="high" if urgent else None,
                estimated_minutes=minutes,
                time_window=window,
                start=start,
            )
        )

    if not tasks and not events:
        return empty_result("unknown")
    return empty_result("add", tasks=tasks, events=events)
