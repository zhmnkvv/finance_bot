"""Сквозные тесты через настоящий Dispatcher aiogram: апдейт → хендлер → FSM → БД → ответ.

HTTP-запросы к Telegram подменены FakeSession, поэтому сеть не нужна.
"""

import itertools
from datetime import datetime
from decimal import Decimal

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage

from finance_bot.bot.handlers.errors import USER_ERROR_TEXT
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from finance_bot.bot import keyboards as kb
from finance_bot.bot.app import build_dispatcher
from finance_bot.bot.scheduler import process_reminders
from finance_bot.config import Settings
from finance_bot.db.models import Base, Category, Debt, Reminder, Transaction, User
from finance_bot.db.seed import ensure_default_categories
from finance_bot.utils.dates import now_utc


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.answered: set[str] = set()
        self.double_answers = 0
        self._ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, AnswerCallbackQuery):
            # Как настоящий Telegram: второй ответ на тот же callback — ошибка 400
            if method.callback_query_id in self.answered:
                self.double_answers += 1
                raise TelegramBadRequest(method=method, message="query is too old or query ID is invalid")
            self.answered.add(method.callback_query_id)
        if isinstance(method, (SendMessage, EditMessageText, SendPhoto)):
            return Message(
                message_id=next(self._ids),
                date=datetime.now(),
                chat=Chat(id=getattr(method, "chat_id", None) or 1, type="private"),
                text=getattr(method, "text", None),
            )
        return True

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


class Harness:
    def __init__(self, dp, bot, factory):
        self.dp, self.bot, self.factory = dp, bot, factory
        self.session: FakeSession = bot.session
        self.expect_error = False
        self._upd = itertools.count(1)
        self._msg = itertools.count(1)

    def tg_user(self, uid):
        return TgUser(id=uid, is_bot=False, first_name=f"U{uid}")

    async def text(self, text: str, uid: int = 1):
        msg = Message(message_id=next(self._msg), date=datetime.now(), chat=Chat(id=uid, type="private"),
                      from_user=self.tg_user(uid), text=text)  # fmt: skip
        await self.dp.feed_update(self.bot, Update(update_id=next(self._upd), message=msg))

    async def press(self, cb_data, uid: int = 1):
        data = cb_data.pack() if hasattr(cb_data, "pack") else cb_data
        message = Message(message_id=next(self._msg), date=datetime.now(), chat=Chat(id=uid, type="private"),
                          from_user=TgUser(id=42, is_bot=True, first_name="bot"), text="…")  # fmt: skip
        cb = CallbackQuery(id=str(next(self._upd)), from_user=self.tg_user(uid), chat_instance="ci", data=data,
                           message=message)  # fmt: skip
        await self.dp.feed_update(self.bot, Update(update_id=next(self._upd), callback_query=cb))

    def last_text(self) -> str:
        for m in reversed(self.session.requests):
            if isinstance(m, (SendMessage, EditMessageText)):
                return m.text
        return ""

    def last_answer(self) -> str | None:
        for m in reversed(self.session.requests):
            if isinstance(m, AnswerCallbackQuery):
                return m.text
        return None

    async def count(self, model) -> int:
        async with self.factory() as s:
            return await s.scalar(select(func.count(model.id)))

    async def category_id(self, kind, code) -> int:
        async with self.factory() as s:
            return await s.scalar(select(Category.id).where(Category.kind == kind, Category.code == code,
                                                            Category.user_id.is_(None)))  # fmt: skip


class FactoryHolder:
    """Роутеры aiogram подключаются к диспетчеру один раз — диспетчер общий, а БД у каждого теста своя."""

    factory = None

    def __call__(self):
        return self.factory()


HOLDER = FactoryHolder()
DP = build_dispatcher(Settings(bot_token="42:TEST", default_timezone="Asia/Qyzylorda"), HOLDER)


@pytest.fixture
async def h():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await ensure_default_categories(s)
        await s.commit()
    HOLDER.factory = factory
    DP.fsm.storage = MemoryStorage()
    bot = Bot("42:TEST", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    harness = Harness(DP, bot, factory)
    yield harness
    await engine.dispose()
    assert harness.session.double_answers == 0, "callback answered twice"
    if not harness.expect_error:
        assert not any(USER_ERROR_TEXT in (getattr(m, "text", "") or "") for m in harness.session.requests), \
            "handler crashed (user saw the generic error message)"


async def test_start_shows_menu(h):
    await h.text("/start")
    assert "Finance Tracker" in h.last_text()


async def test_add_expense_wizard_and_double_confirm(h):
    await h.text(kb.BTN_EXPENSE)
    assert "Введи сумму" in h.last_text()
    await h.text("-100")
    assert "отрицательной" in h.last_text()
    await h.text("abc")
    assert "Не понял сумму" in h.last_text()
    await h.text("2 500")
    assert "категорию" in h.last_text()
    await h.press(kb.Flow(action="cat", value=await h.category_id("expense", "food")))
    assert "комментарий" in h.last_text().lower()
    await h.press(kb.Flow(action="skip"))
    assert "2 500 ₸" in h.last_text() and "Еда" in h.last_text()
    await h.press(kb.Flow(action="save"))
    assert "Сохранено" in h.last_text()
    assert await h.count(Transaction) == 1

    # Повторное нажатие «Сохранить» не создаёт дубль
    await h.press(kb.Flow(action="save"))
    assert await h.count(Transaction) == 1
    assert "уже сохранена" in (h.last_answer() or "")


async def test_add_income_with_comment_via_menu(h):
    await h.press(kb.Menu(action="income"))
    await h.text("350000")
    await h.press(kb.Flow(action="cat", value=await h.category_id("income", "salary")))
    await h.text("Ноябрь")
    assert "Ноябрь" in h.last_text()
    await h.press(kb.Flow(action="save"))
    async with h.factory() as s:
        tx = await s.scalar(select(Transaction))
    assert (tx.kind, tx.amount, tx.comment, tx.currency) == ("income", Decimal("350000.00"), "Ноябрь", "KZT")


async def test_cancel_at_any_step(h):
    await h.text(kb.BTN_EXPENSE)
    await h.text("700")
    await h.press(kb.Flow(action="cancel"))
    assert "отменено" in h.last_text()
    await h.text(kb.BTN_INCOME)
    await h.text("/cancel")
    assert "отменено" in h.last_text()
    assert await h.count(Transaction) == 0


async def test_quick_add_with_budget_alert(h):
    await h.text("/start")
    await h.press(kb.BudgetCb(action="cat", id=await h.category_id("expense", "food")))
    await h.text("10000")
    assert "Бюджет создан" in "".join(m.text for m in h.session.requests if isinstance(m, SendMessage))

    await h.text("8500 обед в кафе")
    assert "Еда" in h.last_text() and "8 500" in h.last_text()
    await h.press(kb.Flow(action="save"))
    assert "⚠️ Бюджет" in h.last_text() and "85%" in h.last_text()

    await h.text("2000 продукты")
    await h.press(kb.Flow(action="save"))
    assert "превышен" in h.last_text()


async def test_quick_add_unknown_category_asks(h):
    await h.text("999 непонятно что")
    assert "категорию" in h.last_text()
    await h.press(kb.Flow(action="cat", value=await h.category_id("expense", "other")))
    assert "Всё верно?" in h.last_text()
    await h.press(kb.Flow(action="save"))
    assert await h.count(Transaction) == 1


async def test_edit_and_delete_from_card(h):
    await h.text("1000 такси")
    await h.press(kb.Flow(action="save"))
    async with h.factory() as s:
        tx_id = await s.scalar(select(Transaction.id))
    await h.press(kb.TxCb(action="amount", id=tx_id))
    await h.text("1200")
    assert "Изменено" in h.last_text() and "1 200" in h.last_text()
    await h.press(kb.TxCb(action="setcat", id=tx_id, value=await h.category_id("expense", "fun")))
    assert "Развлечения" in h.last_text()
    await h.press(kb.TxCb(action="del", id=tx_id))
    await h.press(kb.TxCb(action="del_yes", id=tx_id))
    assert await h.count(Transaction) == 0
    await h.press(kb.TxCb(action="del_yes", id=tx_id))  # повторное нажатие — без ошибки
    assert "Уже удалено" in (h.last_answer() or "")


async def test_other_user_cannot_open_or_delete(h):
    await h.text("1000 такси", uid=1)
    await h.press(kb.Flow(action="save"), uid=1)
    async with h.factory() as s:
        tx_id = await s.scalar(select(Transaction.id))
    await h.press(kb.TxCb(action="open", id=tx_id), uid=2)
    assert "не найдена" in (h.last_answer() or "")
    await h.press(kb.TxCb(action="del_yes", id=tx_id), uid=2)
    assert await h.count(Transaction) == 1
    await h.press(kb.Menu(action="balance"), uid=2)
    assert "Текущий баланс: <b>0 ₸</b>" in h.last_text()


async def test_history_pagination_and_stats_chart(h):
    for i in range(10):
        await h.text(f"{100 + i} кофе")
        await h.press(kb.Flow(action="save"))
    await h.press(kb.Menu(action="history"))
    assert "стр. 1/2" in h.last_text()
    await h.press(kb.HistCb(page=1))
    assert "стр. 2/2" in h.last_text()
    await h.press(kb.HistCb(kind="i"))
    assert "Операций не найдено" in h.last_text()

    await h.press(kb.StatsCb(action="cats"))
    assert any(isinstance(m, SendPhoto) for m in h.session.requests), "должен прийти график"
    await h.press(kb.StatsCb(action="overview"))
    assert "Этот месяц" in h.last_text()


async def test_debt_flow_partial_and_full(h):
    await h.press(kb.DebtCb(action="new_owed"))
    await h.text("Асан")
    await h.text("20000")
    await h.press(kb.Flow(action="d_today"))
    await h.press(kb.Flow(action="skip"))  # без срока
    await h.press(kb.Flow(action="skip"))  # без комментария
    await h.press(kb.Flow(action="save_debt"))
    assert "Долг сохранён" in h.last_text()
    async with h.factory() as s:
        debt_id = await s.scalar(select(Debt.id))

    await h.press(kb.DebtCb(action="part", id=debt_id))
    await h.text("25000")
    assert "больше остатка" in h.last_text()
    await h.text("5000")
    assert "Остаток: <b>15 000 ₸</b>" in h.last_text()
    await h.press(kb.DebtCb(action="full_yes", id=debt_id))
    await h.press(kb.DebtCb(action="full_yes", id=debt_id))  # повторно
    async with h.factory() as s:
        debt = await s.get(Debt, debt_id)
        assert debt.status == "closed" and len(debt.payments) == 2


async def test_reminder_flow_and_delivery(h):
    await h.press(kb.RemCb(action="new"))
    await h.press(kb.RemCb(action="kind", value="internet"))
    await h.text("7990")
    await h.press(kb.Flow(action="d_tomorrow"))
    await h.press(kb.RemCb(action="time", value="10-00"))
    await h.press(kb.RemCb(action="repeat", value="monthly"))
    await h.press(kb.RemCb(action="save"))
    assert await h.count(Reminder) == 1

    async with h.factory() as s:
        rem = await s.scalar(select(Reminder))
        first_run = rem.next_run_at
    sent = await process_reminders(h.bot, h.factory, first_run)
    assert sent == 1 and "Оплатить интернет" in h.last_text()
    assert await process_reminders(h.bot, h.factory, first_run) == 0  # второй раз не шлём
    async with h.factory() as s:
        rem = await s.scalar(select(Reminder))
        assert rem.next_run_at > first_run and rem.is_active

    await h.press(kb.RemCb(action="record", id=rem.id))
    await h.press(kb.RemCb(action="record", id=rem.id))  # повторно
    async with h.factory() as s:
        txs = list(await s.scalars(select(Transaction)))
    assert len(txs) == 1 and txs[0].amount == Decimal("7990.00")


async def test_settings_currency(h):
    await h.text("/start")
    await h.press(kb.SetCb(action="setcur", value="USD"))
    assert "Доллар" in h.last_text()
    await h.text("15 coffee")
    await h.press(kb.Flow(action="cat", value=await h.category_id("expense", "food")))
    await h.press(kb.Flow(action="save"))
    async with h.factory() as s:
        assert (await s.scalar(select(Transaction.currency))) == "USD"
        assert (await s.scalar(select(func.count(User.id)))) == 1


async def test_database_error_is_handled_without_traceback(h, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from finance_bot.services.transactions import TransactionService

    async def broken(*a, **kw):
        raise OperationalError("INSERT ...", {}, Exception("database is locked"))

    h.expect_error = True
    monkeypatch.setattr(TransactionService, "create", broken)
    await h.text("500 кофе")
    await h.press(kb.Flow(action="save"))
    texts_sent = [getattr(m, "text", "") or "" for m in h.session.requests]
    alerts = [m.text for m in h.session.requests if isinstance(m, AnswerCallbackQuery) and m.text]
    assert any(USER_ERROR_TEXT in t for t in texts_sent + alerts), "пользователь должен увидеть понятную ошибку"
    assert not any("Traceback" in t or "OperationalError" in t or "locked" in t for t in texts_sent + alerts)
    assert await h.count(Transaction) == 0
    # После сбоя бот продолжает работать
    monkeypatch.undo()
    await h.text("/menu")
    assert "Главное меню" in h.last_text()


@pytest.mark.parametrize(
    "data", ["tx:open:abc", "tx:open", "h:zzz:0:all:0:0:0:list", "b:open:-1", "d:open:999999999", "garbage", "s:setcur:BTC", "h:a:0:c:99999999:1:0:list", "h:a:0:all:0:0:-3:list"]
)
async def test_malformed_or_foreign_callbacks_are_safe(h, data):
    await h.text("/start")
    await h.press(data)
    assert h.last_answer() is not None or h.last_text()  # ответили и не упали


@pytest.mark.parametrize("bad", ["0", "-5", "1e9", "12.345", "99999999999999", "сто", "<b>1</b>"])
async def test_bad_amount_input_reprompts(h, bad):
    await h.text(kb.BTN_EXPENSE)
    await h.text(bad)
    assert "⚠️" in h.last_text()
    await h.text("100")  # сценарий продолжается после ошибки
    assert "категорию" in h.last_text()


async def test_html_in_comment_is_escaped(h):
    await h.text("100 <b>кофе</b> & <script>")
    await h.press(kb.Flow(action="cat", value=await h.category_id("expense", "food")))
    assert "&lt;b&gt;" in h.last_text() and "<script>" not in h.last_text()


async def test_unknown_text_shows_hint(h):
    await h.text("привет")
    assert "500 кофе" in h.last_text()


def test_now_utc_is_aware():
    assert now_utc().tzinfo is not None
