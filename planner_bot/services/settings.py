"""Изменение настроек обычным текстом. Регулярки надёжнее LLM для таких коротких команд."""

import re

from planner_bot.db.models import User
from planner_bot.utils.dates import is_valid_tz, parse_hhmm

T = r"(\d{1,2}(?:[:.]\d{2})?)"
RE_TZ = re.compile(r"^(?:/tz|/timezone|часовой\s+пояс|таймзона|timezone)\s*[:=]?\s*([A-Za-z_]+/[A-Za-z_\-+/]+|UTC)\s*$", re.I)
RE_MORNING = re.compile(rf"^(?:утренний\s+план|утро|план\s+утром)\s+(?:в\s+)?{T}\s*$", re.I)
RE_EVENING = re.compile(rf"^(?:итоги(?:\s+дня)?|вечерние\s+итоги|вечер)\s+(?:в\s+)?{T}\s*$", re.I)
RE_DAY = re.compile(rf"^(?:день|рабочий\s+день|активное\s+время)\s+с\s+{T}\s+до\s+{T}\s*$", re.I)
RE_REMIND = re.compile(r"^напоминать\s+за\s+(\d{1,3})\s*мин\w*\s*$", re.I)
RE_NOTIFY = re.compile(r"^уведомления\s+(вкл|выкл|включить|выключить|on|off)\w*\s*$", re.I)


def apply_settings_text(user: User, text: str) -> str | None:
    """Если текст — команда настроек, применяет её и возвращает ответ. Иначе None."""
    t = text.strip()
    if m := RE_TZ.match(t):
        tz = m.group(1)
        if not is_valid_tz(tz):
            return "Не знаю такой часовой пояс. Пример: <code>Europe/Moscow</code>, <code>Asia/Almaty</code>."
        user.timezone = tz
        return f"🌍 Часовой пояс: <code>{tz}</code>"
    if m := RE_MORNING.match(t):
        if not (v := parse_hhmm(m.group(1))):
            return "Не понял время. Пример: «утренний план в 7:30»"
        user.morning_time = v
        user.last_morning_sent = None
        return f"☀️ Утренний план буду присылать в {v:%H:%M}"
    if m := RE_EVENING.match(t):
        if not (v := parse_hhmm(m.group(1))):
            return "Не понял время. Пример: «итоги в 22:00»"
        user.evening_time = v
        user.last_evening_sent = None
        return f"🌙 Итоги дня буду присылать в {v:%H:%M}"
    if m := RE_DAY.match(t):
        a, b = parse_hhmm(m.group(1)), parse_hhmm(m.group(2))
        if not a or not b or a >= b:
            return "Не понял интервал. Пример: «день с 7 до 23»"
        user.day_start, user.day_end = a, b
        return f"🕗 Планирую дела с {a:%H:%M} до {b:%H:%M}"
    if m := RE_REMIND.match(t):
        n = int(m.group(1))
        if not 1 <= n <= 180:
            return "Можно от 1 до 180 минут."
        user.reminder_minutes = n
        return f"⏰ Буду напоминать за {n} мин"
    if m := RE_NOTIFY.match(t):
        on = m.group(1).lower() in {"вкл", "включить", "on"}
        user.notifications_enabled = on
        return "🔔 Уведомления включены" if on else "🔕 Уведомления выключены"
    return None
