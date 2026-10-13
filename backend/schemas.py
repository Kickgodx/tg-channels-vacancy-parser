"""Pydantic-схемы REST API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tg_jobs_parser.ai.schema import MatchBreakdown, SkillMatch
from tg_jobs_parser.models.metrics import MetricsSummary, SalaryDisplayMeta
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_progress import ParseProgressSnapshot

RunStatus = Literal["pending", "queued", "running", "completed", "cancelled", "failed"]


class _RunOverrides(BaseModel):
    """Whitelist overrides прогона: неизвестное поле → 422, а не молчаливый merge в конфиг.

    Всё, чего здесь нет (``ai.base_url``, лимиты и concurrency, путь к Telethon-сессии),
    берётся только из серверного ``config.yaml``.
    """

    model_config = ConfigDict(extra="forbid")


class RunSearchOverrides(_RunOverrides):
    date_from: str | None = Field(default=None, max_length=32)
    date_to: str | None = Field(default=None, max_length=32)


class RunFiltersOverrides(_RunOverrides):
    specializations: list[str] | None = None
    any_specialization: bool | None = None
    exclude_keywords: list[str] | None = None
    countries: list[str] | None = None
    cities: list[str] | None = None
    work_formats: list[str] | None = None


class RunParserOverrides(_RunOverrides):
    vacancy_score_threshold: int | None = None
    vacancy_min_substantive_score: int | None = Field(default=None, ge=0)


class RunAiOverrides(_RunOverrides):
    enabled: bool | None = None
    # id из llm_models.yaml — проверяется в build_config.
    model: str | None = Field(default=None, min_length=1)
    fallback_models: list[str] | None = None
    use_cache: bool | None = None


class RunCreateRequest(_RunOverrides):
    channels: list[str] | None = None
    search: RunSearchOverrides | None = None
    filters: RunFiltersOverrides | None = None
    parser: RunParserOverrides | None = None
    ai: RunAiOverrides | None = None


class RunCreateResponse(BaseModel):
    run_id: uuid.UUID
    status: RunStatus


class RunSummary(BaseModel):
    id: uuid.UUID
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    posts_fetched: int = 0
    parse_warnings: str | None = None
    unique_vacancies: int | None = None


class RunDetail(RunSummary):
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    run_params: dict[str, Any] | None = None
    metrics: MetricsSummary | None = None
    error_message: str | None = None
    progress: dict[str, Any] | None = None
    progress_updated_at: datetime | None = None
    cancel_requested: bool = False


class ProgressEvent(BaseModel):
    run_id: uuid.UUID
    snapshot: ParseProgressSnapshot


class VacancyListItem(BaseModel):
    """Карточка в списке: без raw/cleaned_text, только короткий preview."""

    id: str
    source_post_key: str
    source_channel: str
    source_url: str
    post_url: str | None = None
    post_date: datetime | None = None
    preview: str = Field(description="Укороченный cleaned_text для таблицы (~120 символов).")

    detected_specializations: list[str] = Field(default_factory=list)
    detected_grade: str = "unknown"
    detected_salary_min: float | None = None
    detected_salary_max: float | None = None
    detected_salary_avg: float | None = None
    detected_salary_avg_rub: float | None = None
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

    extraction_source: str = "rules"
    ai_confidence: float | None = None
    ai_notes: str | None = None
    vacancy_score: int = 0
    vacancy_substantive_score: int = 0

    is_duplicate: bool = False
    duplicate_group_id: str | None = None
    duplicate_reason: str | None = None

    match_score: float | None = None
    match_explanation: str | None = None

    parsed_at: datetime
    archived_at: datetime | None = None


class VacancyListResponse(BaseModel):
    items: list[VacancyListItem]
    total: int
    page: int
    page_size: int


VacancySortField = Literal[
    "post_date",
    "detected_salary_avg",
    "detected_salary_avg_rub",
    "work_format",
    "detected_grade",
    "salary_currency",
    "vacancy_score",
    "match_score",
]
VacancySortOrder = Literal["asc", "desc"]


class VacancyQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)
    source_channel: str | None = None
    grade: str | None = None
    specializations: list[str] = Field(
        default_factory=list,
        description="Вакансии с любой из перечисленных специализаций в detected_specializations",
    )
    work_format: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    has_salary: bool | None = Field(
        default=None,
        description="True — только вакансии с указанной зарплатой (min/max/avg).",
    )
    q: str | None = Field(default=None, description="Поиск по cleaned_text (ILIKE)")
    include_archived: bool = Field(
        default=False,
        description="True — включить архивные вакансии (старше TTL) в выдачу.",
    )
    sort_by: VacancySortField = "post_date"
    sort_order: VacancySortOrder = "desc"
    resume_id: int | None = Field(
        default=None,
        description="Подмешать LLM-оценку соответствия этому резюме (join vacancy_matches).",
    )


class HealthResponse(BaseModel):
    status: str
    database: bool
    config_loaded: bool


class SalaryGroupStats(BaseModel):
    """Квантили зарплаты (RUB/мес) внутри группы (грейд, формат, специализация)."""

    group: str
    count: int
    mean: float | None = None
    min: float | None = None
    q1: float | None = None
    median: float | None = None
    q3: float | None = None
    max: float | None = None


class SalaryMonthlyBucket(BaseModel):
    period: str
    count: int
    mean: float | None = None
    median: float | None = None


class SalaryPeriodGroupBucket(BaseModel):
    """Квантили зарплаты внутри пары (период × группа): год/месяц × грейд|формат."""

    period: str
    group: str
    count: int
    mean: float | None = None
    median: float | None = None
    min: float | None = None
    q1: float | None = None
    q3: float | None = None
    max: float | None = None


class SalaryDistributionBucket(BaseModel):
    range: str
    count: int


class SalaryAnalytics(BaseModel):
    by_grade: list[SalaryGroupStats] = Field(default_factory=list)
    by_work_format: list[SalaryGroupStats] = Field(default_factory=list)
    by_specialization: list[SalaryGroupStats] = Field(default_factory=list)
    monthly: list[SalaryMonthlyBucket] = Field(default_factory=list)
    yearly: list[SalaryMonthlyBucket] = Field(default_factory=list)
    by_grade_yearly: list[SalaryPeriodGroupBucket] = Field(default_factory=list)
    by_work_format_yearly: list[SalaryPeriodGroupBucket] = Field(default_factory=list)
    by_grade_monthly: list[SalaryPeriodGroupBucket] = Field(default_factory=list)
    by_work_format_monthly: list[SalaryPeriodGroupBucket] = Field(default_factory=list)
    distribution: list[SalaryDistributionBucket] = Field(default_factory=list)
    salary_display: SalaryDisplayMeta | None = None


class SourceStats(BaseModel):
    """Качество канала: доля уникальных вакансий, средний score, покрытие зарплатой."""

    channel: str
    vacancies: int
    duplicates: int
    unique_share: float
    avg_score: float | None = None
    with_salary_share: float


class SourceAnalytics(BaseModel):
    items: list[SourceStats] = Field(default_factory=list)


class CompanyStats(BaseModel):
    company: str
    vacancies: int
    avg_score: float | None = None
    with_salary: int
    avg_salary_rub: float | None = None


class CompanyAnalytics(BaseModel):
    items: list[CompanyStats] = Field(default_factory=list)


class SavedFilterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    params: VacancyQuery = Field(default_factory=VacancyQuery)


class SavedFilterOut(BaseModel):
    id: int
    name: str
    params: VacancyQuery
    created_at: datetime


class SavedFilterListResponse(BaseModel):
    items: list[SavedFilterOut] = Field(default_factory=list)


class ResumeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class ResumeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    text: str | None = Field(default=None, min_length=1)


class ResumeOut(BaseModel):
    id: int
    name: str
    text: str
    created_at: datetime


class ResumeListResponse(BaseModel):
    items: list[ResumeOut] = Field(default_factory=list)


class VacancyMatchOut(BaseModel):
    """Оценка одной пары резюме↔вакансия (соло-матчинг из карточки вакансии)."""

    resume_id: int
    vacancy_id: str
    overall_score: int = Field(description="0-100: итоговый score, считает backend.")
    confidence: int = Field(description="0-100: уверенность LLM в качестве оценки, не сам score.")
    breakdown: MatchBreakdown
    explanation: str | None = None
    matching_skills: list[SkillMatch] = Field(default_factory=list)
    missing_skills: list[SkillMatch] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    model: str
    created_at: datetime
    cached: bool = Field(
        default=False,
        description="True — оценка взята из БД, без нового вызова LLM.",
    )


CoverLetterTone = Literal["neutral", "friendly", "formal"]


class CoverLetterRequest(BaseModel):
    """Параметры генерации сопроводительного письма (тело POST)."""

    tone: CoverLetterTone = "neutral"
    extra_notes: str | None = Field(
        default=None,
        max_length=1000,
        description="Свободные пожелания к письму (акценты, что упомянуть).",
    )


class CoverLetterOut(BaseModel):
    """Сгенерированное письмо для пары резюме↔вакансия."""

    resume_id: int
    vacancy_id: str
    text: str
    tone: str
    model: str
    created_at: datetime
    cached: bool = Field(
        default=False,
        description="True — текст взят из БД, без нового вызова LLM.",
    )


class LlmJobAccepted(BaseModel):
    """Ответ 202: LLM-задача поставлена в очередь."""

    job_id: uuid.UUID
    status: str = "queued"


class LlmJobOut(BaseModel):
    """Статус фоновой LLM-задачи; при done — готовый match или cover_letter."""

    id: uuid.UUID
    kind: str
    status: str
    resume_id: int
    vacancy_id: str
    error_message: str | None = None
    match: VacancyMatchOut | None = None
    cover_letter: CoverLetterOut | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class VacancyImportRequest(BaseModel):
    """Ручной импорт: тексты вакансий (например, скопированные с HH) + опциональная ссылка."""

    texts: list[str] = Field(min_length=1, max_length=50)
    source_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Ссылка на оригинал (hh.ru и т.п.) — сохраняется как post_url.",
    )


VacancyImportStatus = Literal["created", "exists", "not_vacancy", "error"]


class VacancyImportItemResult(BaseModel):
    index: int
    status: VacancyImportStatus
    message: str | None = None
    vacancy: Vacancy | None = None


class VacancyImportResponse(BaseModel):
    items: list[VacancyImportItemResult]
    created: int
    existing: int
    rejected: int
    ai_enabled: bool
    warning: str | None = None


class AuthCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    is_admin: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="TTL access-токена в секундах")
    user: UserOut
