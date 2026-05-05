"""Результат полного цикла парсинга."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import AliasChoices, BaseModel, Field

from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.vacancy import Vacancy


class ParseResult(BaseModel):
    """Посты, отфильтрованные вакансии, дубликаты и метрики."""

    run_params: dict = Field(default_factory=dict)
    posts_fetched: int = 0
    parse_warnings: str | None = Field(
        default=None,
        validation_alias=AliasChoices("web_limitation_warning", "parse_warnings"),
        description="Предупреждения прогона (лимиты дат, каналы, Telethon и т.д.).",
    )
    vacancies_all: list[Vacancy] = Field(default_factory=list)
    vacancies_unique: list[Vacancy] = Field(default_factory=list)
    duplicates: list[Vacancy] = Field(default_factory=list)
    metrics: MetricsSummary = Field(default_factory=MetricsSummary)
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
