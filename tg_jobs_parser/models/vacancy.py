"""Модель вакансии."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from tg_jobs_parser.extraction.grade_extractor import normalize_grade


class Vacancy(BaseModel):
    """Вакансия, спарсенная из поста Telegram."""

    id: str = Field(description="Канонический id: sha256(normalize(cleaned_text))")
    source_post_key: str = Field(
        description="Идентификатор поста в Telegram: {channel}-{post_id}",
    )
    source_channel: str = Field(description="Username канала без @")
    source_url: str = Field(description="Базовая ссылка на канал (t.me/s/...)")
    post_url: str | None = None
    post_date: datetime | None = None
    raw_text: str
    cleaned_text: str

    detected_specializations: list[str] = Field(default_factory=list)
    detected_grade: str = Field(
        default="unknown",
        description="intern…principal или unknown, если в тексте не найдено",
    )
    detected_salary_min: float | None = None
    detected_salary_max: float | None = None
    detected_salary_avg: float | None = None
    detected_salary_avg_rub: float | None = Field(
        default=None,
        description="Зарплата в RUB по последнему курсу ЦБ (на сегодня / из кэша).",
    )
    salary_currency: str | None = None

    work_format: str | None = Field(
        default=None,
        description="remote | hybrid | office | relocation | unknown",
    )
    country: str | None = None
    city: str | None = None
    company: str | None = None
    contact: str | None = None

    technologies: list[str] = Field(default_factory=list)
    required_experience: str | None = None
    employment_type: str | None = None

    application_urls: list[str] = Field(
        default_factory=list,
        description="Ссылки на отклик (hh.ru, careers, формы и т.д.), не t.me поста.",
    )
    extraction_source: str = Field(
        default="rules",
        description="rules | ai | hybrid",
    )
    ai_confidence: float | None = Field(default=None, ge=0, le=1)
    ai_notes: str | None = Field(
        default=None,
        description="Краткие пояснения от LLM (поле notes в VacancyAIExtraction).",
    )

    vacancy_score: int = 0
    vacancy_substantive_score: int = Field(
        default=0,
        description=(
            "Сумма heuristic-баллов без бонуса за email/@; см. vacancy_min_substantive_score в конфиге"
        ),
    )

    is_duplicate: bool = False
    duplicate_group_id: str | None = None
    duplicate_reason: str | None = None

    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    archived_at: datetime | None = Field(
        default=None,
        description="Soft delete: вакансия старше TTL скрыта из выдачи, но не удалена.",
    )

    model_config = {"frozen": False}

    @field_validator("detected_grade", mode="before")
    @classmethod
    def coerce_grade(cls, v: object) -> str:
        return normalize_grade(v)
