"""Опциональный LLM-слой структурирования вакансий.

Тяжёлые импорты (enricher, openrouter) — только из подмодулей, чтобы не было
цикла ``config.schema`` ↔ ``ai``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "RunAIStats",
    "VacancyAIExtraction",
    "enrich_vacancy_with_ai",
]

if TYPE_CHECKING:
    from tg_jobs_parser.ai.enricher import RunAIStats, enrich_vacancy_with_ai
    from tg_jobs_parser.ai.schema import VacancyAIExtraction


def __getattr__(name: str) -> Any:
    if name == "RunAIStats":
        from tg_jobs_parser.ai.enricher import RunAIStats

        return RunAIStats
    if name == "enrich_vacancy_with_ai":
        from tg_jobs_parser.ai.enricher import enrich_vacancy_with_ai

        return enrich_vacancy_with_ai
    if name == "VacancyAIExtraction":
        from tg_jobs_parser.ai.schema import VacancyAIExtraction

        return VacancyAIExtraction
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
