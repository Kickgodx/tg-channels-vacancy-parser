"""Pydantic-схемы REST API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tg_jobs_parser.models.metrics import MetricsSummary
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
    work_format: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = Field(default=None, description="Поиск по cleaned_text (ILIKE)")
    sort_by: VacancySortField = "post_date"
    sort_order: VacancySortOrder = "desc"


class HealthResponse(BaseModel):
    status: str
    database: bool
    config_loaded: bool
