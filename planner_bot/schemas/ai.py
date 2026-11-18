"""Схема «сырого» ответа LLM.

Это контракт для structured output: все поля обязательные, но допускают null,
а даты — строки. Строгая проверка и приведение типов делаются позже,
в services/ai/validation.py — LLM не доверяем.
"""

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "add",  # добавить задачи и/или события
    "show_plan",  # показать/составить план на день
    "show_tasks",  # показать список задач
    "reschedule",  # перенести задачу
    "complete",  # отметить выполненной
    "delete",  # удалить задачу
    "stats",  # статистика
    "help",  # что умеет бот
    "smalltalk",  # приветствие, благодарность и т. п.
    "unknown",
]


class AITask(BaseModel):
    title: str = Field(description="Короткое название задачи с заглавной буквы, без слов «надо», «нужно»")
    due_date: str | None = Field(description="День выполнения YYYY-MM-DD, если назван («завтра купить»)")
    deadline: str | None = Field(
        description="Крайний срок: YYYY-MM-DD или YYYY-MM-DDTHH:MM в локальном времени пользователя"
    )
    priority: Literal["low", "medium", "high"] | None
    estimated_minutes: int | None = Field(description="Оценка длительности в минутах, если её можно разумно оценить")
    time_window: Literal["morning", "afternoon", "evening"] | None = Field(
        description="Желаемое время суток («вечером» → evening)"
    )
    start: str | None = Field(description="Точное время начала YYYY-MM-DDTHH:MM, только если пользователь его назвал")


class AIEvent(BaseModel):
    title: str = Field(description="Название события с заглавной буквы («Пара», «Работа»)")
    start: str = Field(description="Начало YYYY-MM-DDTHH:MM в локальном времени")
    end: str | None = Field(description="Конец YYYY-MM-DDTHH:MM в локальном времени")
    description: str | None


class AIReschedule(BaseModel):
    date: str | None = Field(description="Новый день YYYY-MM-DD")
    after_time: str | None = Field(description="«после 18» → 18:00 (HH:MM)")
    exact_time: str | None = Field(description="Точное новое время HH:MM")


class AIParseResult(BaseModel):
    intent: Intent
    tasks: list[AITask]
    events: list[AIEvent]
    target_title: str | None = Field(
        description="Для reschedule/complete/delete: название задачи, о которой речь, или null, если не названа"
    )
    reschedule: AIReschedule | None
    plan_date: str | None = Field(description="Для show_plan: день YYYY-MM-DD")
    clarification_question: str | None = Field(
        description="Короткий уточняющий вопрос, если без него нельзя выполнить просьбу"
    )


def empty_result(intent: Intent = "unknown", **kwargs) -> AIParseResult:
    data = dict(
        intent=intent,
        tasks=[],
        events=[],
        target_title=None,
        reschedule=None,
        plan_date=None,
        clarification_question=None,
    )
    data.update(kwargs)
    return AIParseResult(**data)
