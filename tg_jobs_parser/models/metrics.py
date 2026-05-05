"""Модели метрик и аналитики."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TimeSeriesBucket(BaseModel):
    """Значение метрики по периоду."""

    period: str
    count: int


class SalaryStats(BaseModel):
    mean: float | None = None
    median: float | None = None
    count_with_salary: int = 0


class MetricsSummary(BaseModel):
    """Сводные метрики запуска."""

    total_posts_fetched: int = 0
    total_vacancies: int = 0
    unique_vacancies: int = 0
    duplicate_count: int = 0
    vacancies_by_channel: dict[str, int] = Field(default_factory=dict)
    vacancies_by_specialization: dict[str, int] = Field(default_factory=dict)
    vacancies_by_grade: dict[str, int] = Field(default_factory=dict)
    vacancies_by_country: dict[str, int] = Field(default_factory=dict)
    vacancies_by_city: dict[str, int] = Field(default_factory=dict)
    vacancies_by_work_format: dict[str, int] = Field(default_factory=dict)

    daily: list[TimeSeriesBucket] = Field(default_factory=list)
    weekly: list[TimeSeriesBucket] = Field(default_factory=list)
    monthly: list[TimeSeriesBucket] = Field(default_factory=list)

    salary_by_specialization: dict[str, SalaryStats] = Field(default_factory=dict)
    salary_by_grade: dict[str, SalaryStats] = Field(default_factory=dict)
    salary_by_specialization_grade: dict[str, SalaryStats] = Field(default_factory=dict)

    vacancies_with_salary: int = 0
    vacancies_without_salary: int = 0

    grade_distribution: dict[str, int] = Field(default_factory=dict)
    mean_grade_rank_by_specialization: dict[str, float] = Field(default_factory=dict)

    technology_top: dict[str, int] = Field(default_factory=dict)
    technology_by_specialization: dict[str, dict[str, int]] = Field(default_factory=dict)
    technology_mentions: dict[str, int] = Field(default_factory=dict)

    warnings: list[str] = Field(default_factory=list)
