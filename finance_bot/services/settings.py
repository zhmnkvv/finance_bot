from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from finance_bot.config import SUPPORTED_CURRENCIES
from finance_bot.db.models import User
from finance_bot.services.reminders import ReminderService
from finance_bot.utils.dates import get_tz, is_valid_tz


class SettingsError(ValueError):
    pass


class SettingsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def set_currency(self, user: User, currency: str) -> None:
        currency = currency.upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise SettingsError("Эта валюта не поддерживается.")
        # Старые операции сохраняют свою валюту — конвертации по выдуманным курсам нет
        user.settings.currency = currency
        await self.session.flush()

    async def set_timezone(self, user: User, tz_name: str) -> None:
        if not is_valid_tz(tz_name):
            raise SettingsError("Не знаю такой часовой пояс. Пример: Asia/Almaty, Europe/Moscow.")
        old = get_tz(user.settings.timezone)
        user.settings.timezone = tz_name
        await ReminderService(self.session).retime_for_timezone(user, old, get_tz(tz_name))

    async def toggle_notifications(self, user: User) -> bool:
        user.settings.notifications_enabled = not user.settings.notifications_enabled
        await self.session.flush()
        return user.settings.notifications_enabled

    async def set_debt_reminder_time(self, user: User, t: time) -> None:
        user.settings.debt_reminder_time = t
        await self.session.flush()
