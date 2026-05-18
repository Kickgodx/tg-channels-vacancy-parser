from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.db import repository as repo
from backend.db.vacancy_metrics import aggregate_vacancy_metrics
from backend.schemas import VacancyListResponse, VacancyQuery, VacancySortField, VacancySortOrder
from tg_jobs_parser.models.metrics import MetricsSummary

router = APIRouter(prefix="/vacancies", tags=["vacancies"], dependencies=[Depends(verify_api_key)])


def _vacancy_query_from_params(
    *,
    page: int,
    page_size: int,
    source_channel: str | None,
    grade: str | None,
    work_format: str | None,
    date_from: str | None,
    date_to: str | None,
    q: str | None,
    sort_by: VacancySortField,
    sort_order: VacancySortOrder,
) -> VacancyQuery:
    from datetime import date as date_type

    df = date_type.fromisoformat(date_from) if date_from else None
    dt = date_type.fromisoformat(date_to) if date_to else None
    return VacancyQuery(
        page=page,
        page_size=page_size,
        source_channel=source_channel,
        grade=grade,
        work_format=work_format,
        date_from=df,
        date_to=dt,
        q=q,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/metrics", response_model=MetricsSummary)
def vacancy_metrics(
    source_channel: str | None = None,
    grade: str | None = None,
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
) -> MetricsSummary:
    """Агрегаты для графиков (SQL GROUP BY, без загрузки всех вакансий)."""
    query = _vacancy_query_from_params(
        page=1,
        page_size=1,
        source_channel=source_channel,
        grade=grade,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        q=q,
        sort_by="post_date",
        sort_order="desc",
    )
    return aggregate_vacancy_metrics(db, query)


@router.get("", response_model=VacancyListResponse)
def list_vacancies(
    page: int = 1,
    page_size: int = 50,
    source_channel: str | None = None,
    grade: str | None = None,
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    db: Session = Depends(get_db),
) -> VacancyListResponse:
    query = _vacancy_query_from_params(
        page=page,
        page_size=page_size,
        source_channel=source_channel,
        grade=grade,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        q=q,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items, total = repo.list_vacancies(db, query)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)
