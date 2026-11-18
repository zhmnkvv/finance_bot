"""Inline-кнопки: напоминания, перенос, итоги дня."""

import logging
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.bot import keyboards as kb
from planner_bot.bot.actions import Actions, Reply
from planner_bot.db.models import OPEN_STATUSES, User
from planner_bot.services.planner import PlannerService
from planner_bot.services.tasks import TaskService
from planner_bot.utils.dates import now_utc

log = logging.getLogger(__name__)
router = Router(name="callbacks")


async def _drop_buttons(cb: CallbackQuery) -> None:
    """Убираем кнопки у исходного сообщения, чтобы не нажимали повторно."""
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass  # сообщение слишком старое или уже без кнопок


@router.callback_query(kb.TaskCb.filter())
async def on_task_action(cb: CallbackQuery, callback_data: kb.TaskCb, session: AsyncSession, user: User) -> None:
    task = await TaskService(session).get(user, callback_data.task_id)
    if task is None:
        await cb.answer("Задача не найдена — возможно, её уже удалили.", show_alert=True)
        await _drop_buttons(cb)
        return
    if task.status not in OPEN_STATUSES and callback_data.action != "del":
        await cb.answer("Эта задача уже закрыта.")
        await _drop_buttons(cb)
        return

    actions = Actions(session, user, now_utc())
    user.context_task_id = task.id
    action = callback_data.action
    reply: Reply | None = None

    if action == "start":
        reply = Reply(f"💪 Удачи! Начинаем «{escape(task.title)}».")
    elif action == "done":
        reply = await actions.complete_task(task)
    elif action == "cancel":
        await TaskService(session).cancel(task)
        reply = Reply(f"❌ Отменил «{escape(task.title)}».")
    elif action == "postpone":
        await cb.answer()
        await _drop_buttons(cb)
        await cb.message.answer("Куда перенести?", reply_markup=kb.postpone_kb(task.id))
        return
    elif action == "r_evening":
        reply = await actions.reschedule_evening(task)
    elif action == "r_tomorrow":
        reply = await actions.reschedule_tomorrow(task)
    elif action == "r_pick":
        user.pending_action = f"pick_time:{task.id}"
        reply = Reply("Напиши, когда: например «15:30», «завтра в 10» или «в пятницу после 18».")
    elif action == "del":
        reply = await actions.delete_task(task)
    else:
        log.warning("unknown task action %s", action)

    await cb.answer()
    await _drop_buttons(cb)
    if reply:
        await cb.message.answer(reply.text, reply_markup=reply.markup)


@router.callback_query(kb.SummaryCb.filter())
async def on_summary(cb: CallbackQuery, callback_data: kb.SummaryCb, session: AsyncSession, user: User) -> None:
    await cb.answer()
    await _drop_buttons(cb)
    if callback_data.action == "move":
        moved = await PlannerService(session).move_open_to_tomorrow(user)
        text = f"📆 Перенёс на завтра задач: {len(moved)}." if moved else "Нечего переносить 🙂"
    else:
        text = "Хорошо, оставляю как есть. Спокойной ночи! 🌙"
    await cb.message.answer(text)
