"""Настройки приложения. Единственный секрет — токен Telegram-бота, он читается из .env."""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_CURRENCIES = ("KZT", "USD", "EUR", "RUB")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    database_url: str = "sqlite+aiosqlite:///./finance.db"
    default_timezone: str = "Asia/Qyzylorda"
    default_currency: str = "KZT"
    log_level: str = "INFO"
    # Как часто фоновый планировщик проверяет напоминания (секунды)
    scheduler_interval_seconds: int = Field(default=30, ge=5)

    @field_validator("default_timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Unknown timezone: {v}") from e
        return v

    @field_validator("default_currency")
    @classmethod
    def _valid_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency: {v}")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
