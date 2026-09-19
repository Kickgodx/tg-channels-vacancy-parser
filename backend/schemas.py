"""Pydantic-схемы REST API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tg_jobs_parser.models.metrics import MetricsSummary, SalaryDisplayMeta
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_progress import ParseProgressSnapshot


RunStatus = Literal["pending", "running", "completed", "cancelled", "failed"]


class RunCreateRequest(BaseModel):
    channels: list[str] | None = None
    search: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    parser: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None
    telegram_session_file: str | None = None


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


class ProgressEvent(BaseModel):
    run_id: uuid.UUID
    snapshot: ParseProgressSnapshot


class VacancyListResponse(BaseModel):
    items: list[Vacancy]
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


class SalaryDistributionBucket(BaseModel):
    range: str
    count: int


class SalaryAnalytics(BaseModel):
    by_grade: list[SalaryGroupStats] = Field(default_factory=list)
    by_work_format: list[SalaryGroupStats] = Field(default_factory=list)
    by_specialization: list[SalaryGroupStats] = Field(default_factory=list)
    monthly: list[SalaryMonthlyBucket] = Field(default_factory=list)
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
