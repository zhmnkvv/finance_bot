from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from planner_bot.db.models import Task

BTN_TODAY = "📅 Сегодня"
BTN_TASKS = "📝 Задачи"
BTN_ADD = "➕ Добавить"
BTN_STATS = "📊 Статистика"
BTN_SETTINGS = "⚙️ Настройки"


class TaskCb(CallbackData, prefix="t"):
    action: str  # start|done|postpone|cancel|r_evening|r_tomorrow|r_pick|del
    task_id: int


class SummaryCb(CallbackData, prefix="s"):
    action: str  # move|keep


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_TASKS)],
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напиши, что планируешь…",
    )


def _btn(text: str, action: str, task_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=TaskCb(action=action, task_id=task_id).pack())


def before_start_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("▶️ Начать", "start", task_id), _btn("🕐 Перенести", "postpone", task_id)]]
    )


def after_end_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("✅ Выполнено", "done", task_id)],
            [_btn("🕐 Перенести", "postpone", task_id), _btn("❌ Отменить", "cancel", task_id)],
        ]
    )


def postpone_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🌆 Сегодня вечером", "r_evening", task_id)],
            [_btn("📆 Завтра", "r_tomorrow", task_id)],
            [_btn("⌨️ Выбрать время", "r_pick", task_id)],
        ]
    )


def summary_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, на завтра", callback_data=SummaryCb(action="move").pack()),
                InlineKeyboardButton(text="Нет", callback_data=SummaryCb(action="keep").pack()),
            ]
        ]
    )


def tasks_kb(tasks: list[Task]) -> InlineKeyboardMarkup | None:
    """Быстрые кнопки «выполнено» под списком задач."""
    rows = [[_btn(f"✅ {t.title[:30]}", "done", t.id)] for t in tasks[:8]]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def choose_task_kb(tasks: list[Task], action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t.title[:40], action, t.id)] for t in tasks[:8]])
