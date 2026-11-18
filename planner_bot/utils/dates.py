"""Работа с датами и часовыми поясами. Чистые функции — легко тестировать."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS_RU = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "среду": 2, "ср": 2,
    "четверг": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "субботы": 5, "сб": 5,
    "воскресенье": 6, "воскресенья": 6, "вс": 6,
    "понедельника": 0, "вторника": 1, "среды": 2, "четверга": 3,
}  # fmt: skip

WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]  # fmt: skip


def get_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_tz(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz)


def local_dt(d: date, t: time, tz: ZoneInfo) -> datetime:
    """Локальные дата+время пользователя → aware datetime."""
    return datetime.combine(d, t, tzinfo=tz)


def day_bounds_utc(d: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def end_of_day(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(23, 59), tzinfo=tz)


def next_weekday(today: date, weekday: int, allow_today: bool = False) -> date:
    """Ближайший указанный день недели. «В пятницу», сказанное в пятницу, — следующая пятница."""
    delta = (weekday - today.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return today + timedelta(days=delta)


_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?$")


def parse_hhmm(value: str) -> time | None:
    """'9' → 09:00, '18:30' / '18.30' → 18:30."""
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    if h == 24 and mnt == 0:
        return time(23, 59)
    if 0 <= h <= 23 and 0 <= mnt <= 59:
        return time(h, mnt)
    return None


def parse_local_iso(value: str, tz: ZoneInfo) -> datetime:
    """ISO-строка от LLM → aware datetime. Наивное время трактуем как локальное время пользователя."""
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value.strip()[:10])


def round_up(dt: datetime, minutes: int = 5) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    rem = dt.minute % minutes
    return dt if rem == 0 else dt + timedelta(minutes=minutes - rem)


def human_date(d: date, today: date) -> str:
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    if d == today - timedelta(days=1):
        return "вчера"
    return f"{WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTHS_GEN[d.month - 1]}"
