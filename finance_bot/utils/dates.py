"""Даты, часовые пояса, периоды. Чистые функции — легко тестировать."""

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
              "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]  # fmt: skip
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]  # fmt: skip
WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


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


def local_dt(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz)


def day_start_utc(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(UTC)


@dataclass(frozen=True)
class Period:
    """Полуинтервал [start, end) в UTC + подпись для пользователя."""

    start: datetime
    end: datetime
    label: str


def month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt


def period(kind: str, now: datetime, tz: ZoneInfo) -> Period:
    """day | week | month | prev_month | year — в календарных границах пояса пользователя."""
    today = now.astimezone(tz).date()
    if kind == "day":
        a, b, label = today, today + timedelta(days=1), "Сегодня"
    elif kind == "week":
        a = today - timedelta(days=today.weekday())
        b, label = a + timedelta(days=7), "Эта неделя"
    elif kind == "month":
        a, b = month_bounds(today.year, today.month)
        label = f"{MONTHS_NOM[today.month - 1]} {today.year}"
    elif kind == "prev_month":
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        a, b = month_bounds(y, m)
        label = f"{MONTHS_NOM[m - 1]} {y}"
    elif kind == "year":
        a, b, label = date(today.year, 1, 1), date(today.year + 1, 1, 1), f"{today.year} год"
    else:
        raise ValueError(f"unknown period {kind}")
    return Period(day_start_utc(a, tz), day_start_utc(b, tz), label)


def custom_period(a: date, b: date, tz: ZoneInfo) -> Period:
    """Включительно [a, b]."""
    if b < a:
        a, b = b, a
    return Period(day_start_utc(a, tz), day_start_utc(b + timedelta(days=1), tz), f"{fmt_date(a)} – {fmt_date(b)}")


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def human_date(d: date, today: date) -> str:
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    if d == today - timedelta(days=1):
        return "вчера"
    year = f" {d.year}" if d.year != today.year else ""
    return f"{d.day} {MONTHS_GEN[d.month - 1]}{year}"


_DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$")


def parse_user_date(text: str, today: date) -> date | None:
    """'сегодня' / 'вчера' / 'завтра' / '25.11' / '25.11.2026' → date."""
    t = text.strip().lower()
    words = {"сегодня": 0, "вчера": -1, "позавчера": -2, "завтра": 1, "послезавтра": 2}
    if t in words:
        return today + timedelta(days=words[t])
    m = _DATE_RE.match(t)
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    y = int(m.group(3)) if m.group(3) else today.year
    if y < 100:
        y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


_PERIOD_RE = re.compile(r"^\s*(\S+)\s*[-–—]\s*(\S+)\s*$")


def parse_user_period(text: str, today: date) -> tuple[date, date] | None:
    """'01.11.2026 - 15.11.2026' / '1.11-15.11'."""
    m = _PERIOD_RE.match(text)
    if not m:
        return None
    a, b = parse_user_date(m.group(1), today), parse_user_date(m.group(2), today)
    if a is None or b is None:
        return None
    return (a, b) if a <= b else (b, a)


_TIME_RE = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?$")


def parse_hhmm(value: str) -> time | None:
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    if 0 <= h <= 23 and 0 <= mnt <= 59:
        return time(h, mnt)
    return None


def add_months(d: date, months: int, anchor_day: int | None = None) -> date:
    """Сдвиг на N месяцев; 31-е число в коротком месяце → последний день месяца."""
    m0 = d.month - 1 + months
    y, m = d.year + m0 // 12, m0 % 12 + 1
    day = min(anchor_day or d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)
