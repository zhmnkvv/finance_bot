"""Сборка и запуск бота."""

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from planner_bot.bot.handlers import routers
from planner_bot.bot.handlers.errors import on_error
from planner_bot.bot.middlewares import DbSessionMiddleware
from planner_bot.bot.scheduler import run_scheduler
from planner_bot.config import Settings, get_settings
from planner_bot.db.session import create_engine, create_session_factory, init_db
from planner_bot.logging_setup import setup_logging
from planner_bot.services.ai import MessageInterpreter
from planner_bot.services.ai.parser import AIParser

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="today", description="📅 План на сегодня"),
    BotCommand(command="tasks", description="📝 Мои задачи"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="Что умеет бот"),
]


def build_interpreter(settings: Settings) -> MessageInterpreter:
    if not settings.ai_enabled:
        log.warning("ANTHROPIC_API_KEY is not set — AI parsing disabled, using rule-based parser only")
        return MessageInterpreter(None)
    parser = AIParser(
        api_key=settings.anthropic_api_key.get_secret_value(),
        model=settings.anthropic_model,
        timeout=settings.ai_timeout_seconds,
    )
    log.info("AI parsing enabled, model=%s", settings.anthropic_model)
    return MessageInterpreter(parser)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings.database_url)
    await init_db(engine)
    factory = create_session_factory(engine)

    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.update.outer_middleware(DbSessionMiddleware(factory, settings.default_timezone))
    dp.errors.register(on_error)
    dp.include_routers(*routers)

    me = await bot.get_me()
    log.info("bot @%s started", me.username)
    try:
        await bot.set_my_commands(COMMANDS)
    except Exception:
        log.warning("could not set bot commands", exc_info=True)

    scheduler = asyncio.create_task(run_scheduler(bot, factory, settings.scheduler_interval_seconds))
    try:
        await dp.start_polling(
            bot,
            interpreter=build_interpreter(settings),
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await bot.session.close()
        await engine.dispose()
