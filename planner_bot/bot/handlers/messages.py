"""Команды, кнопки меню и основной обработчик обычного текста."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from planner_bot.bot import formatting as fmt
from planner_bot.bot import keyboards as kb
from planner_bot.bot.actions import Actions, Reply
from planner_bot.db.models import ProcessedMessage, User
from planner_bot.schemas.commands import ParsedCommand
from planner_bot.services.ai import MessageInterpreter
from planner_bot.services.ai.time_input import parse_time_answer
from planner_bot.services.settings import apply_settings_text
from planner_bot.services.tasks import TaskService
from planner_bot.utils.dates import get_tz, now_utc

log = logging.getLogger(__name__)
router = Router(name="messages")

MAX_TEXT_LEN = 2000


async def send(message: Message, reply: Reply) -> None:
    await message.answer(reply.text, reply_markup=reply.markup or kb.main_menu())


@router.message(CommandStart())
async def cmd_start(message: Message, user: User) -> None:
    name = f", {message.from_user.first_name}" if message.from_user.first_name else ""
    await message.answer(
        f"Привет{name}! 👋\n\n{fmt.HELP_TEXT}\n\n"
        f"🌍 Твой часовой пояс сейчас: <code>{user.timezone}</code>. "
        "Если он другой — напиши, например, «часовой пояс Europe/Moscow».",
        reply_markup=kb.main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(fmt.HELP_TEXT, reply_markup=kb.main_menu())


@router.message(Command("settings"))
@router.message(F.text == kb.BTN_SETTINGS)
async def cmd_settings(message: Message, user: User) -> None:
    await message.answer(fmt.format_settings(user), reply_markup=kb.main_menu())


@router.message(Command("today"))
@router.message(F.text == kb.BTN_TODAY)
async def cmd_today(message: Message, session: AsyncSession, user: User) -> None:
    actions = Actions(session, user, now_utc())
    await send(message, await actions.show_plan(ParsedCommand(intent="show_plan")))


@router.message(Command("tasks"))
@router.message(F.text == kb.BTN_TASKS)
async def cmd_tasks(message: Message, session: AsyncSession, user: User) -> None:
    await send(message, await Actions(session, user, now_utc()).show_tasks())


@router.message(Command("stats"))
@router.message(F.text == kb.BTN_STATS)
async def cmd_stats(message: Message, session: AsyncSession, user: User) -> None:
    await send(message, await Actions(session, user, now_utc()).stats())


@router.message(F.text == kb.BTN_ADD)
async def cmd_add(message: Message) -> None:
    await message.answer(
        "Просто напиши, что нужно сделать или куда идти — обычным текстом. Например:\n"
        "• «Завтра купить продукты»\n"
        "• «В пятницу встреча с 15 до 16»\n"
        "• «До воскресенья подготовить доклад, это часа 3»",
        reply_markup=kb.main_menu(),
    )


async def _already_processed(session: AsyncSession, message: Message) -> bool:
    """Telegram может прислать апдейт повторно (рестарт, сетевой сбой) — не создаём дубли."""
    exists = await session.scalar(
        select(ProcessedMessage.id).where(
            ProcessedMessage.chat_id == message.chat.id, ProcessedMessage.message_id == message.message_id
        )
    )
    if exists:
        return True
    session.add(ProcessedMessage(chat_id=message.chat.id, message_id=message.message_id))
    await session.flush()
    return False


@router.message(F.text)
async def on_text(message: Message, session: AsyncSession, user: User, interpreter: MessageInterpreter) -> None:
    if await _already_processed(session, message):
        log.info("duplicate message %s/%s ignored", message.chat.id, message.message_id)
        return
    text = message.text.strip()
    if len(text) > MAX_TEXT_LEN:
        await message.answer("Сообщение слишком длинное 🙈 Разбей, пожалуйста, на несколько частей.")
        return

    if (settings_reply := apply_settings_text(user, text)) is not None:
        await message.answer(settings_reply, reply_markup=kb.main_menu())
        return

    now = now_utc()
    actions = Actions(session, user, now)

    # Ждём ответа на «⌨️ Выбрать время»
    if user.pending_action and user.pending_action.startswith("pick_time:"):
        task_id = int(user.pending_action.split(":", 1)[1])
        user.pending_action = None
        spec = parse_time_answer(text, now.astimezone(get_tz(user.timezone)).date())
        task = await TaskService(session).get(user, task_id)
        if spec is not None and task is not None:
            await send(message, await actions.apply_reschedule(task, spec))
            return
        # Не похоже на время — обрабатываем как обычное сообщение

    await message.bot.send_chat_action(message.chat.id, "typing")
    open_titles = [t.title for t in await TaskService(session).list_open(user, limit=30)]
    interpretation = await interpreter.interpret(text, now=now, tz_name=user.timezone, open_task_titles=open_titles)
    log.info(
        "message user=%s source=%s intent=%s tasks=%d events=%d",
        user.id,
        interpretation.source,
        interpretation.command.intent,
        len(interpretation.command.tasks),
        len(interpretation.command.events),
    )
    reply = await actions.execute(interpretation.command)
    if interpretation.ai_failed and interpretation.command.intent == "unknown":
        reply = Reply(
            "AI-помощник сейчас недоступен 😔 Я понимаю простые фразы вроде "
            "«Завтра пара с 10 до 12» или «Что у меня сегодня?». Попробуй ещё раз чуть позже."
        )
    await send(message, reply)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Я пока понимаю только текст ✍️", reply_markup=kb.main_menu())
