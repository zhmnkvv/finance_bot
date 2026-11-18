"""Сборка и запуск бота."""

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from finance_bot.bot.handlers import routers
from finance_bot.bot.handlers.errors import on_error
from finance_bot.bot.middlewares import DbSessionMiddleware
from finance_bot.bot.scheduler import run_scheduler
from finance_bot.config import Settings, get_settings
from finance_bot.db.session import create_engine, create_session_factory, init_db
from finance_bot.logging_setup import setup_logging

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="menu", description="🏠 Главное меню"),
    BotCommand(command="balance", description="💰 Мой баланс"),
    BotCommand(command="cancel", description="❌ Отменить текущее действие"),
    BotCommand(command="help", description="Как пользоваться"),
]


def build_dispatcher(settings: Settings, factory) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware(factory, settings.default_timezone, settings.default_currency))
    dp.errors.register(on_error)
    dp.include_routers(*routers)
    return dp


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    await init_db(engine, factory, settings.database_url)

    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, factory)

    me = await bot.get_me()
    log.info("bot @%s started", me.username)
    try:
        await bot.set_my_commands(COMMANDS)
    except Exception:
        log.warning("could not set bot commands", exc_info=True)

    scheduler = asyncio.create_task(run_scheduler(bot, factory, settings.scheduler_interval_seconds))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await bot.session.close()
        await engine.dispose()
