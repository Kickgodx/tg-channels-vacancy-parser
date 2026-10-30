from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, verify_api_key
from backend.api.params import analytics_filters
from backend.db import analytics, technology_analytics, technology_graph, vacancy_lifetime
from backend.db.aggregate_cache import cached_aggregate
from backend.schemas import (
    CompanyAnalytics,
    LifetimeAnalytics,
    SalaryAnalytics,
    SourceAnalytics,
    TechnologyAnalytics,
    TechnologyGraph,
    VacancyQuery,
)

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)


@router.get("/salaries", response_model=SalaryAnalytics)
def salary_analytics(
    query: VacancyQuery = Depends(analytics_filters),
    db: Session = Depends(get_db),
) -> SalaryAnalytics:
    """Квантили зарплат по грейдам/формату/специализациям, месячная динамика, распределение."""
    return cached_aggregate("salaries", query, lambda: analytics.salary_analytics(db, query))


@router.get("/sources", response_model=SourceAnalytics)
def source_stats(
    query: VacancyQuery = Depends(analytics_filters),
    db: Session = Depends(get_db),
) -> SourceAnalytics:
    """Качество каналов: уникальные вакансии, дубли, средний score, покрытие зарплатой."""
    return cached_aggregate("sources", query, lambda: SourceAnalytics(items=analytics.source_quality(db, query)))


@router.get("/companies", response_model=CompanyAnalytics)
def company_stats(
    query: VacancyQuery = Depends(analytics_filters),
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
    query: VacancyQuery = Depends(analytics_filters),
    db: Session = Depends(get_db),
) -> TechnologyAnalytics:
    """Технологии: зарплатная премия с поправкой на грейд, спрос по месяцам, рост и падение.

    Фильтр по технологии не применяется — премия под ним не имеет смысла.
    """
    return cached_aggregate("technologies", query, lambda: technology_analytics.technology_analytics(db, query))


@router.get("/technology-graph", response_model=TechnologyGraph)
def technology_graph_stats(
    query: VacancyQuery = Depends(analytics_filters),
    nodes: int = Query(default=technology_graph.DEFAULT_NODES, ge=5, le=technology_graph.MAX_NODES),
    db: Session = Depends(get_db),
) -> TechnologyGraph:
    """Какие технологии просят вместе: частые технологии и пары с lift (свой фильтр по технологии не применяется)."""
    return cached_aggregate(
        "technology_graph", query, lambda: technology_graph.technology_graph(db, query, nodes=nodes), nodes=nodes
    )


@router.get("/lifetime", response_model=LifetimeAnalytics)
def lifetime_stats(
    query: VacancyQuery = Depends(analytics_filters),
    db: Session = Depends(get_db),
) -> LifetimeAnalytics:
    """Жизнь вакансии по публикациям в Telegram: повторные публикации, срок, роли, компании, долгожители."""
    return cached_aggregate("lifetime", query, lambda: vacancy_lifetime.vacancy_lifetime(db, query))
