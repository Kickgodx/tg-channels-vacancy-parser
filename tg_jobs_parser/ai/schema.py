"""Схема JSON-ответа LLM."""

from __future__ import annotations

from typing import Literal

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
        from tg_jobs_parser.fx.convert import parse_salary_currency

        # Сохраняем период (…/HOUR): normalize_currency оставляет только ISO.
        iso, period = parse_salary_currency(str(v).strip())
        if not iso:
            return None
        if period == "hour":
            return f"{iso}/HOUR"
        if period == "year":
            return f"{iso}/YEAR"
        return iso


Importance = Literal["critical", "important", "secondary"]

_IMPORTANCE_ALIASES = {
    "critical": "critical",
    "required": "critical",
    "must": "critical",
    "must_have": "critical",
    "mandatory": "critical",
    "important": "important",
    "nice": "secondary",
    "nice_to_have": "secondary",
    "optional": "secondary",
    "secondary": "secondary",
}


def _clamp_score(v: object) -> int:
    """Модели иногда отдают float/str — приводим к int 0..100."""
    try:
        n = int(round(float(str(v).strip())))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _coerce_skill_match(v: object) -> object:
    """Терпим формат ``["python"]`` — строка превращается в SkillMatch."""
    if isinstance(v, str):
        return {"skill": v.strip(), "importance": "important", "evidence": ""}
    return v


class SkillMatch(BaseModel):
    """Один навык в matching/missing: название, приоритет требования, обоснование."""

    skill: str
    importance: Importance = "important"
    evidence: str = ""

    @field_validator("importance", mode="before")
    @classmethod
    def norm_importance(cls, v: object) -> str:
        key = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
        return _IMPORTANCE_ALIASES.get(key, "important")

    @field_validator("skill", "evidence", mode="before")
    @classmethod
    def norm_str(cls, v: object) -> str:
        return str(v).strip() if v is not None else ""


class MatchBreakdown(BaseModel):
    """Оценки LLM по измерениям; итоговый score считает backend."""

    role_match: int = Field(default=0, ge=0, le=100)
    skills_match: int = Field(default=0, ge=0, le=100)
    seniority_match: int = Field(default=0, ge=0, le=100)
    domain_match: int = Field(default=0, ge=0, le=100)
    work_format_match: int = Field(default=0, ge=0, le=100)

    @field_validator(
        "role_match",
        "skills_match",
        "seniority_match",
        "domain_match",
        "work_format_match",
        mode="before",
    )
    @classmethod
    def norm_score(cls, v: object) -> int:
        return _clamp_score(v)


class VacancyMatchExtraction(BaseModel):
    """Оценка соответствия резюме вакансии (ответ LLM матчинга).

    Модель оценивает только измерения и объясняет; ``overall_score``
    вычисляется детерминированно через :func:`calculate_overall_score`.
    """

    confidence: int = Field(
        default=50, ge=0, le=100,
        description="0-100: уверенность в качестве оценки (полнота резюме/вакансии), не сам score.",
    )
    breakdown: MatchBreakdown = Field(default_factory=MatchBreakdown)
    matching_skills: list[SkillMatch] = Field(default_factory=list, max_length=8)
    missing_skills: list[SkillMatch] = Field(default_factory=list, max_length=8)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    risks: list[str] = Field(default_factory=list, max_length=5)
    explanation: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def norm_confidence(cls, v: object) -> int:
        return _clamp_score(v)

    @field_validator("explanation", mode="before")
    @classmethod
    def norm_explanation(cls, v: object) -> str:
        return _coerce_optional_str(v) or ""

    @field_validator("matching_skills", "missing_skills", mode="before")
    @classmethod
    def norm_skill_list_fields(cls, v: object) -> list:
        if not isinstance(v, list):
            return []
        return [_coerce_skill_match(x) for x in v][:8]

    @field_validator("strengths", "risks", mode="before")
    @classmethod
    def norm_str_list_fields(cls, v: object) -> list[str]:
        return _coerce_str_list(v)[:5]


def calculate_overall_score(
    breakdown: MatchBreakdown,
    missing_skills: list[SkillMatch],
) -> int:
    """Детерминированный итоговый score: взвешенная сумма + penalty за critical-пробелы."""
    score = round(
        breakdown.role_match * 0.30
        + breakdown.skills_match * 0.35
        + breakdown.seniority_match * 0.20
        + breakdown.domain_match * 0.10
        + breakdown.work_format_match * 0.05
    )

    critical_missing = sum(1 for s in missing_skills if s.importance == "critical")
    if critical_missing >= 2:
        score = min(score, 59)
    elif critical_missing == 1:
        score = min(score, 69)

    return max(0, min(100, score))
