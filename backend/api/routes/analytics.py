from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, verify_api_key
from backend.api.params import vacancy_filters
from backend.db import analytics, technology_analytics, vacancy_lifetime
from backend.db.aggregate_cache import cached_aggregate
from backend.schemas import (
    CompanyAnalytics,
    LifetimeAnalytics,
    SalaryAnalytics,
    SourceAnalytics,
    TechnologyAnalytics,
    VacancyQuery,
)

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)


@router.get("/salaries", response_model=SalaryAnalytics)
def salary_analytics(
    query: VacancyQuery = Depends(vacancy_filters),
    db: Session = Depends(get_db),
) -> SalaryAnalytics:
    """Квантили зарплат по грейдам/формату/специализациям, месячная динамика, распределение."""
    return cached_aggregate("salaries", query, lambda: analytics.salary_analytics(db, query))


@router.get("/sources", response_model=SourceAnalytics)
def source_stats(
    query: VacancyQuery = Depends(vacancy_filters),
    db: Session = Depends(get_db),
) -> SourceAnalytics:
    """Качество каналов: уникальные вакансии, дубли, средний score, покрытие зарплатой."""
    return cached_aggregate("sources", query, lambda: SourceAnalytics(items=analytics.source_quality(db, query)))


@router.get("/companies", response_model=CompanyAnalytics)
def company_stats(
    query: VacancyQuery = Depends(vacancy_filters),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CompanyAnalytics:
    """Топ работодателей по числу вакансий."""
    return cached_aggregate(
        "companies",
        query,
        lambda: CompanyAnalytics(items=analytics.top_companies(db, query, limit=limit)),
        limit=limit,
    )


@router.get("/technologies", response_model=TechnologyAnalytics)
def technology_stats(
    query: VacancyQuery = Depends(vacancy_filters),
    db: Session = Depends(get_db),
) -> TechnologyAnalytics:
    """Технологии: зарплатная премия с поправкой на грейд, спрос по месяцам, рост и падение.

    Фильтр по технологии не применяется — премия под ним не имеет смысла.
    """
    return cached_aggregate("technologies", query, lambda: technology_analytics.technology_analytics(db, query))


@router.get("/lifetime", response_model=LifetimeAnalytics)
def lifetime_stats(
    query: VacancyQuery = Depends(vacancy_filters),
    db: Session = Depends(get_db),
) -> LifetimeAnalytics:
    """Жизнь вакансии по публикациям в Telegram: повторные публикации, срок, роли, компании, долгожители."""
    return cached_aggregate("lifetime", query, lambda: vacancy_lifetime.vacancy_lifetime(db, query))
