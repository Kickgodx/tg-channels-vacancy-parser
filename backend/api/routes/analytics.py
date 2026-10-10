from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.api.params import vacancy_query_from_params
from backend.db import analytics
from backend.schemas import CompanyAnalytics, SalaryAnalytics, SourceAnalytics

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(verify_api_key)])


def _filters(
    source_channel: str | None,
    grade: str | None,
    specialization: list[str],
    work_format: str | None,
    date_from: str | None,
    date_to: str | None,
    has_salary: bool | None,
    q: str | None,
    include_archived: bool,
):
    return vacancy_query_from_params(
        source_channel=source_channel,
        grade=grade,
        specialization=specialization,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        has_salary=has_salary,
        q=q,
        include_archived=include_archived,
    )


@router.get("/salaries", response_model=SalaryAnalytics)
def salary_analytics(
    source_channel: str | None = None,
    grade: str | None = None,
    specialization: list[str] = Query(default=[]),
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_salary: bool | None = None,
    q: str | None = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
) -> SalaryAnalytics:
    """Квантили зарплат по грейдам/формату/специализациям, месячная динамика, распределение."""
    query = _filters(
        source_channel,
        grade,
        specialization,
        work_format,
        date_from,
        date_to,
        has_salary,
        q,
        include_archived,
    )
    return analytics.salary_analytics(db, query)


@router.get("/sources", response_model=SourceAnalytics)
def source_stats(
    source_channel: str | None = None,
    grade: str | None = None,
    specialization: list[str] = Query(default=[]),
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_salary: bool | None = None,
    q: str | None = None,
    include_archived: bool = False,
    db: Session = Depends(get_db),
) -> SourceAnalytics:
    """Качество каналов: уникальные вакансии, дубли, средний score, покрытие зарплатой."""
    query = _filters(
        source_channel,
        grade,
        specialization,
        work_format,
        date_from,
        date_to,
        has_salary,
        q,
        include_archived,
    )
    return SourceAnalytics(items=analytics.source_quality(db, query))


@router.get("/companies", response_model=CompanyAnalytics)
def company_stats(
    source_channel: str | None = None,
    grade: str | None = None,
    specialization: list[str] = Query(default=[]),
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_salary: bool | None = None,
    q: str | None = None,
    include_archived: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> CompanyAnalytics:
    """Топ работодателей по числу вакансий."""
    query = _filters(
        source_channel,
        grade,
        specialization,
        work_format,
        date_from,
        date_to,
        has_salary,
        q,
        include_archived,
    )
    return CompanyAnalytics(items=analytics.top_companies(db, query, limit=limit))
