"""Результат полного цикла парсинга."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import AliasChoices, BaseModel, Field

from jobdesk.models.metrics import MetricsSummary
from jobdesk.models.vacancy import Vacancy
from jobdesk.parsers.base import ChannelCheckpoint, ForwardedPost


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
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    incomplete_reason: str | None = Field(
        default=None,
        description=(
            "Прогон дошёл до конца, но часть постов осталась неразобранной (LLM отключён "
            "или много ошибок LLM в режиме primary). Собранные вакансии валидны; статус прогона — partial."
        ),
    )
    channel_checkpoints: dict[str, ChannelCheckpoint] = Field(
        default_factory=dict,
        exclude=True,
        description="Новые чекпоинты каналов (инкрементальная вычитка); backend сохраняет их при completed.",
    )
    forwarded_posts: list[ForwardedPost] = Field(
        default_factory=list,
        exclude=True,
        description="Посты, пересланные из других каналов: backend копит по ним кандидатов в источники.",
    )
