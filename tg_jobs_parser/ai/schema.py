"""Схема JSON-ответа LLM."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

_VALID_GRADES = frozenset(
    {"intern", "junior", "middle", "senior", "lead", "principal", "unknown"}
)
_VALID_WORK_FORMATS = frozenset(
    {"remote", "hybrid", "office", "relocation", "unknown"}
)


class VacancyAIExtraction(BaseModel):
    is_job_post: bool = True
    rejection_reason: str | None = None

    detected_specializations: list[str] = Field(default_factory=list)
    detected_grade: str = "unknown"
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    work_format: str | None = None
    country: str | None = None
    city: str | None = None
    company: str | None = None
    contact: str | None = None
    technologies: list[str] = Field(default_factory=list)
    required_experience: str | None = None
    employment_type: str | None = None
    application_urls: list[str] = Field(default_factory=list)

    confidence: float = Field(default=0.5, ge=0, le=1)
    notes: str | None = None

    @field_validator("detected_grade", mode="before")
    @classmethod
    def norm_grade(cls, v: object) -> str:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "unknown"
        g = str(v).strip().lower()
        return g if g in _VALID_GRADES else "unknown"

    @field_validator("work_format", mode="before")
    @classmethod
    def norm_work_format(cls, v: object) -> str | None:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        wf = str(v).strip().lower()
        if wf in _VALID_WORK_FORMATS:
            return wf if wf != "unknown" else None
        return None

    @field_validator("salary_currency", mode="before")
    @classmethod
    def norm_currency(cls, v: object) -> str | None:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        return str(v).strip().upper()[:8]
