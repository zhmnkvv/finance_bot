from aiogram.fsm.state import State, StatesGroup


class AddTx(StatesGroup):
    amount = State()
    category = State()
    comment = State()
    confirm = State()
    date = State()


class EditTx(StatesGroup):
    amount = State()
    comment = State()
    date = State()


class HistoryInput(StatesGroup):
    period = State()


class BudgetInputState(StatesGroup):
    amount = State()


class AddDebt(StatesGroup):
    person = State()
    amount = State()
    opened = State()
    due = State()
    comment = State()
    confirm = State()


class DebtPay(StatesGroup):
    amount = State()


class AddReminder(StatesGroup):
    title = State()
    amount = State()
    date = State()
    time = State()
    repeat = State()
    confirm = State()


class SettingsInput(StatesGroup):
    timezone = State()
    debt_time = State()
