from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.api.params import vacancy_query_from_params
from backend.db import repository as repo
from backend.db.vacancy_metrics import aggregate_vacancy_metrics
from backend.schemas import VacancyListResponse, VacancySortField, VacancySortOrder
from tg_jobs_parser.exporters.excel_exporter import export_excel_bytes
from tg_jobs_parser.models.metrics import MetricsSummary

router = APIRouter(prefix="/vacancies", tags=["vacancies"], dependencies=[Depends(verify_api_key)])


@router.get("/export.xlsx")
def export_vacancies_xlsx(
    source_channel: str | None = None,
    grade: str | None = None,
    specialization: list[str] = Query(default=[]),
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_salary: bool | None = None,
    q: str | None = None,
    include_archived: bool = False,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Excel всех вакансий в БД с учётом фильтров (как в списке)."""
    query = vacancy_query_from_params(
        page=1,
        page_size=500,
        source_channel=source_channel,
        grade=grade,
        specialization=specialization,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        has_salary=has_salary,
        q=q,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    items = repo.list_all_vacancies(db, query)
    metrics = aggregate_vacancy_metrics(db, query)
    data = export_excel_bytes(items, metrics=metrics)
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="vacancies.xlsx"'},
    )


@router.get("/metrics", response_model=MetricsSummary)
def vacancy_metrics(
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
) -> MetricsSummary:
    """Агрегаты для графиков (SQL GROUP BY, без загрузки всех вакансий)."""
    query = vacancy_query_from_params(
        page=1,
        page_size=1,
        source_channel=source_channel,
        grade=grade,
        specialization=specialization,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        has_salary=has_salary,
        q=q,
        include_archived=include_archived,
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
    specialization: list[str] = Query(default=[]),
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_salary: bool | None = None,
    q: str | None = None,
    include_archived: bool = False,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    resume_id: int | None = None,
    db: Session = Depends(get_db),
) -> VacancyListResponse:
    query = vacancy_query_from_params(
        page=page,
        page_size=page_size,
        source_channel=source_channel,
        grade=grade,
        specialization=specialization,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        has_salary=has_salary,
        q=q,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
        resume_id=resume_id,
    )
    items, total = repo.list_vacancies(db, query)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)
