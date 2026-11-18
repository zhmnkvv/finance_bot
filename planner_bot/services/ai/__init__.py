"""Единая точка разбора текста: fast path → LLM → rule-based fallback → валидация."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from planner_bot.schemas.commands import ParsedCommand
from planner_bot.services.ai.parser import AIError, AIParser, AIUnavailableError
from planner_bot.services.ai.rules import fast_intent, rule_parse
from planner_bot.services.ai.validation import to_command
from planner_bot.utils.dates import get_tz

log = logging.getLogger(__name__)


@dataclass
class Interpretation:
    command: ParsedCommand
    source: str  # "fast" | "ai" | "rules"
    ai_failed: bool = False


class MessageInterpreter:
    def __init__(self, ai: AIParser | None):
        self.ai = ai

    async def interpret(
        self, text: str, *, now: datetime, tz_name: str, open_task_titles: list[str]
    ) -> Interpretation:
        tz = get_tz(tz_name)
        now_local = now.astimezone(tz)

        fast = fast_intent(text, now_local.date())
        if fast is not None:
            return Interpretation(to_command(fast, tz, now), "fast")

        ai_failed = False
        if self.ai is not None:
            try:
                raw = await self.ai.parse(
                    text, now_local=now_local, tz_name=tz_name, open_task_titles=open_task_titles
                )
                return Interpretation(to_command(raw, tz, now), "ai")
            except AIError as e:
                ai_failed = True
                level = logging.WARNING if isinstance(e, AIUnavailableError) else logging.ERROR
                log.log(level, "AI parse failed (%s), falling back to rules", e)

        raw = rule_parse(text, now_local)
        return Interpretation(to_command(raw, tz, now), "rules", ai_failed=ai_failed)
