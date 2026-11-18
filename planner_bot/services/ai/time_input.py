"""Разбор ответа на «⌨️ Выбрать время»: «15:30», «завтра в 10», «в пятницу после 18»."""

import re
from datetime import date

from planner_bot.schemas.commands import RescheduleSpec
from planner_bot.services.ai.rules import RE_AFTER_TIME, RE_AT_TIME, find_day
from planner_bot.utils.dates import parse_hhmm

RE_BARE_TIME = re.compile(r"^\s*(\d{1,2}[:.]\d{2})\s*$")


def parse_time_answer(text: str, today: date) -> RescheduleSpec | None:
    t = text.strip().lower()
    day = find_day(t, today)
    if m := RE_AFTER_TIME.search(t):
        return RescheduleSpec(day=day, after_time=parse_hhmm(m.group(1)))
    m = RE_BARE_TIME.match(t) or RE_AT_TIME.search(t)
    if m and (tm := parse_hhmm(m.group(1))):
        return RescheduleSpec(day=day, exact_time=tm)
    if day is not None:
        return RescheduleSpec(day=day)
    return None
