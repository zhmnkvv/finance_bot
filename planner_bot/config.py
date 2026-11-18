"""Настройки приложения. Все секреты читаются из переменных окружения / .env."""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    ai_timeout_seconds: float = 60.0

    database_url: str = "sqlite+aiosqlite:///./planner.db"
    default_timezone: str = "Asia/Qyzylorda"
    log_level: str = "INFO"

    # Как часто фоновый планировщик проверяет напоминания (секунды)
    scheduler_interval_seconds: int = Field(default=30, ge=5)

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, v):
        return v or None

    @field_validator("default_timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Unknown timezone: {v}") from e
        return v

    @property
    def ai_enabled(self) -> bool:
        return self.anthropic_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
