"""Последний роутер: всё, что не поймали остальные."""

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from finance_bot.bot import keyboards as kb

router = Router(name="fallback")


@router.message()
async def any_message(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await message.answer("Выбери вариант кнопкой выше или нажми /cancel, чтобы отменить.")
        return
    await message.answer(
        "Я не понял 🤔\n\nЗапиши операцию одной строкой — <code>500 кофе</code>, "
        "<code>+150000 зарплата</code> — или выбери действие в меню:",
        reply_markup=kb.main_menu(),
    )


@router.callback_query()
async def any_callback(cb: CallbackQuery) -> None:
    await cb.answer("Эта кнопка устарела. Открой меню заново: /menu")
