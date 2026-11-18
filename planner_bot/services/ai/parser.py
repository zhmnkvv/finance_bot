"""Вызов Claude API со structured output. Возвращает только данные — никаких действий с БД."""

from __future__ import annotations

import logging
from datetime import datetime

import anthropic
from pydantic import ValidationError

from planner_bot.schemas.ai import AIParseResult
from planner_bot.services.ai.prompt import SYSTEM_PROMPT, build_user_message

log = logging.getLogger(__name__)


class AIError(Exception):
    """Базовая ошибка AI-слоя. Сообщение — для логов, не для пользователя."""


class AIUnavailableError(AIError):
    """Сеть, лимиты, 5xx, неверный ключ — AI временно недоступен."""


class AIParseError(AIError):
    """Модель вернула ответ, который не прошёл схему, или отказалась отвечать."""


class AIParser:
    def __init__(self, api_key: str, model: str = "claude-opus-5-5", timeout: float = 60.0):
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)

    async def parse(
        self, text: str, *, now_local: datetime, tz_name: str, open_task_titles: list[str]
    ) -> AIParseResult:
        user_msg = build_user_message(text, now_local, tz_name, open_task_titles)
        try:
            response = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=8000,
                # Системный промпт неизменен — кешируем его
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_msg}],
                output_format=AIParseResult,
                # Разбор сообщения — несложная задача, минимальное «размышление» и быстрый ответ
                output_config={"effort": "low"},
                # При отказе модели запрос автоматически повторится на запасной модели
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            log.error("Claude API auth error: %s", e)
            raise AIUnavailableError("auth") from e
        except anthropic.RateLimitError as e:
            log.warning("Claude API rate limited")
            raise AIUnavailableError("rate_limit") from e
        except anthropic.BadRequestError as e:
            log.error("Claude API bad request: %s", e.message)
            raise AIParseError("bad_request") from e
        except anthropic.APIStatusError as e:
            log.warning("Claude API status error %s", e.status_code)
            raise AIUnavailableError(f"status_{e.status_code}") from e
        except anthropic.APIConnectionError as e:
            log.warning("Claude API connection error: %s", e)
            raise AIUnavailableError("connection") from e
        except (ValidationError, ValueError) as e:
            # SDK не смог разобрать JSON модели по схеме
            log.warning("Claude returned invalid structured output: %s", e)
            raise AIParseError("invalid_json") from e

        log.info(
            "AI parse ok request_id=%s stop=%s in=%s out=%s",
            getattr(response, "_request_id", None),
            response.stop_reason,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        if response.stop_reason == "refusal":
            raise AIParseError("refusal")
        if response.stop_reason == "max_tokens":
            raise AIParseError("max_tokens")
        result = response.parsed_output
        if result is None:
            raise AIParseError("empty")
        return result
