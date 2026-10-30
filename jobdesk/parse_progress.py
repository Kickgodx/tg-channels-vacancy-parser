"""Снимки прогресса парсинга для UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jobdesk.models.vacancy import Vacancy

ParseProgressCallback = Callable[["ParseProgressSnapshot"], None]

# Сколько последних уникальных вакансий несёт снимок прогресса (live-таблица в UI).
PROGRESS_PREVIEW_LIMIT = 30


@dataclass
class ParseProgressSnapshot:
    """Промежуточное состояние во время ``run_parse``.

    ``vacancies_found`` — число уникальных на текущий момент (инкрементальный
    дедуп), ``vacancies`` — последние ``PROGRESS_PREVIEW_LIMIT`` из них для
    live-preview.

    Вычитка и обработка идут потоком: пока ``channels_fetched < channels_total``
    (фаза ``fetching``), ``posts_total`` — прочитанное на текущий момент и растёт.
    """

    posts_processed: int
    posts_total: int
    vacancies_found: int
    vacancies: list[Vacancy] = field(default_factory=list)
    phase: str = "processing"
    channels_total: int = 0
    channels_fetched: int = 0
    ai_llm_disabled: bool = False
    ai_llm_disabled_reason: str | None = None
    ai_tokens_in: int = 0
    ai_tokens_out: int = 0
    ai_skipped_score: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
