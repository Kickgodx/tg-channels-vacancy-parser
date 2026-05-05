"""Модель вакансии."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class Vacancy(BaseModel):
    """Вакансия, спарсенная из поста Telegram."""

    id: str
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

    model_config = {"frozen": False}

    @field_validator("detected_grade", mode="before")
    @classmethod
    def coerce_grade(cls, v: object) -> str:
        if v is None:
            return "unknown"
        if isinstance(v, str) and not v.strip():
            return "unknown"
        return str(v).strip() if isinstance(v, str) else "unknown"
