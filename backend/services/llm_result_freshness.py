"""Актуальность сохранённых оценки соответствия, письма и адаптации резюме.

Результат устарел, если с момента запроса сменилась настроенная модель или версия
промпта. Ответ fallback-модели сам по себе не устаревание: сравнивается запрошенная
модель (``requested_model``), а не фактическая (``model``). Строки до миграции 030:
запрошенной считается фактическая, пустая версия по версии не устаревает.

Тон письма сюда не входит — его сравнивает только ``POST`` письма.
"""

from __future__ import annotations

from typing import Protocol

from backend.settings import get_settings
from tg_jobs_parser.ai.prompts import (
    COVER_LETTER_PROMPT_VERSION,
    MATCH_PROMPT_VERSION,
    RESUME_TAILOR_PROMPT_VERSION,
)
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import AiConfig


class _LlmResult(Protocol):
    model: str
    requested_model: str | None
    prompt_version: str | None


def current_ai_config() -> AiConfig:
    """Текущая AI-конфигурация, с которой сравниваются сохранённые результаты."""
    return load_config(get_settings().config_path).ai


def _is_stale(row: _LlmResult, *, configured_model: str, prompt_version: str) -> bool:
    requested = row.requested_model or row.model
    if requested != configured_model:
        return True
    return bool(row.prompt_version) and row.prompt_version != prompt_version


def match_is_stale(row: _LlmResult, ai: AiConfig) -> bool:
    """Оценка получена другой моделью матчинга или другой версией промпта."""
    return _is_stale(row, configured_model=ai.resolved_match_model(), prompt_version=MATCH_PROMPT_VERSION)


def cover_letter_is_stale(row: _LlmResult, ai: AiConfig) -> bool:
    """Письмо получено другой моделью писем или другой версией промпта."""
    return _is_stale(
        row,
        configured_model=ai.resolved_cover_letter_model(),
        prompt_version=COVER_LETTER_PROMPT_VERSION,
    )


def resume_tailoring_is_stale(row: _LlmResult, ai: AiConfig) -> bool:
    """Адаптация получена другой моделью (та же, что у писем) или другой версией промпта."""
    return _is_stale(
        row,
        configured_model=ai.resolved_cover_letter_model(),
        prompt_version=RESUME_TAILOR_PROMPT_VERSION,
    )
