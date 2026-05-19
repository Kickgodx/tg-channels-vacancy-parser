"""Схема JSON-ответа LLM."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from tg_jobs_parser.extraction.grade_extractor import GRADE_OPTIONS, normalize_grade

_VALID_GRADES = frozenset(GRADE_OPTIONS)
_VALID_WORK_FORMATS = frozenset(
    {"remote", "hybrid", "office", "relocation", "unknown"}
)


def _coerce_optional_str(v: object) -> str | None:
    """Строковые поля: модели иногда отдают list вместо str."""
    if v is None:
        return None
    if isinstance(v, list):
        parts = [str(x).strip() for x in v if x is not None and str(x).strip()]
        return "; ".join(parts) if parts else None
    if isinstance(v, dict):
        return None
    s = str(v).strip()
    return s or None


def _coerce_str_list(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, list):
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    s = str(v).strip()
    return [s] if s else []


class VacancyAIExtraction(BaseModel):
    is_job_post: bool = True
    rejection_reason: str | None = None

    detected_specializations: list[str] = Field(
        default_factory=list,
        description=(
            "Primary hired role(s) in this post, usually one entry (e.g. ['Go']). "
            "Do not add QA, analyst, or DevOps from skill/tool mentions unless that role is explicitly hired."
        ),
    )
    detected_grade: str = "unknown"
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = Field(
        default=None,
        description=(
            "RUB, USD, EUR. /HOUR only for explicit hourly pay (₽/час, $/hour). "
            "Monthly: «200 тр», «300к гросс» → RUB without /HOUR."
        ),
    )
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
    notes: str | None = Field(
        default=None,
        description="Краткие пояснения на русском: неоднозначности, пропуски в тексте, допущения; null если нечего добавить.",
    )

    @field_validator(
        "rejection_reason",
        "country",
        "city",
        "company",
        "contact",
        "required_experience",
        "employment_type",
        "notes",
        mode="before",
    )
    @classmethod
    def norm_optional_str_fields(cls, v: object) -> str | None:
        return _coerce_optional_str(v)

    @field_validator(
        "detected_specializations",
        "technologies",
        "application_urls",
        mode="before",
    )
    @classmethod
    def norm_str_list_fields(cls, v: object) -> list[str]:
        return _coerce_str_list(v)

    @field_validator("detected_grade", mode="before")
    @classmethod
    def norm_grade(cls, v: object) -> str:
        return normalize_grade(v)

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
        from tg_jobs_parser.fx.convert import normalize_currency

        return normalize_currency(str(v).strip().upper()[:8], default_if_missing="RUB")
