"""Старт, главное меню, отмена, баланс."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.bot import keyboards as kb
from finance_bot.bot import texts
from finance_bot.bot.handlers.common import show
from finance_bot.db.models import User
from finance_bot.services.stats import StatsService
from finance_bot.utils.dates import now_utc

router = Router(name="menu")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Привет! 👋", reply_markup=kb.reply_menu())
    await message.answer(texts.WELCOME, reply_markup=kb.main_menu())


@router.message(Command("menu"))
@router.message(F.text == kb.BTN_MENU)
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🏠 <b>Главное меню</b>", reply_markup=kb.main_menu())


@router.callback_query(kb.Menu.filter(F.action == "home"))
async def cb_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "🏠 <b>Главное меню</b>", kb.main_menu())


@router.message(Command("cancel"))
@router.message(F.text.lower().in_({"отмена", "отменить", "стоп"}))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    had_state = await state.get_state() is not None
    await state.clear()
    text = "❌ Действие отменено." if had_state else "Нечего отменять 🙂"
    await message.answer(text, reply_markup=kb.main_menu())


@router.callback_query(kb.Flow.filter(F.action == "cancel"))
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "❌ Действие отменено.", kb.main_menu())


async def _balance_text(session: AsyncSession, user: User) -> str:
    return texts.balance(await StatsService(session).balance_summary(user, now_utc()))


def _balance_kb():
    return kb.markup(
        [
            [kb.btn("➕ Доход", kb.Menu(action="income")), kb.btn("➖ Расход", kb.Menu(action="expense"))],
            [kb.btn("📊 Статистика", kb.Menu(action="stats")), kb.HOME],
        ]
    )


@router.message(Command("balance"))
@router.message(F.text == kb.BTN_BALANCE)
async def msg_balance(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await _balance_text(session, user), reply_markup=_balance_kb())


@router.callback_query(kb.Menu.filter(F.action == "balance"))
async def cb_balance(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await show(cb, await _balance_text(session, user), _balance_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Как пользоваться</b>\n\n"
        "• Кнопки меню — для всех действий.\n"
        "• Быстрый ввод одной строкой:\n"
        "  <code>500 кофе</code> — расход\n"
        "  <code>+150000 зарплата</code> — доход\n"
        "  <code>2.5к такси</code> — расход 2 500\n"
        "• /cancel — отменить текущее действие\n"
        "• /balance — баланс, /menu — главное меню",
        reply_markup=kb.main_menu(),
    )
