from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, get_optional_user, verify_api_key
from backend.api.params import vacancy_query_from_params
from backend.db import repository as repo
from backend.db.models import UserRow
from backend.db.vacancy_metrics import aggregate_vacancy_metrics
from backend.schemas import (
    VacancyImportItemResult,
    VacancyImportRequest,
    VacancyImportResponse,
    VacancyListResponse,
    VacancySortField,
    VacancySortOrder,
)
from backend.services import import_service
from tg_jobs_parser.exporters.excel_exporter import export_excel_bytes
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.vacancy import Vacancy

router = APIRouter(prefix="/vacancies", tags=["vacancies"], dependencies=[Depends(verify_api_key)])


@router.post("/import", response_model=VacancyImportResponse)
def import_vacancies(
    body: VacancyImportRequest,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> VacancyImportResponse:
    """Импорт вакансий из текста: rules + LLM-разбор и запись в БД (как в прогоне)."""
    texts = [t.strip() for t in body.texts]
    if not any(texts):
        raise HTTPException(status_code=400, detail="Пустой текст вакансии")
    source_url = (body.source_url or "").strip() or None
    result = import_service.import_vacancy_texts(db, texts, source_url=source_url)
    items = [
        VacancyImportItemResult(index=it.index, status=it.status, message=it.message, vacancy=it.vacancy)
        for it in result.items
    ]
    return VacancyImportResponse(
        items=items,
        created=sum(1 for it in items if it.status == "created"),
        existing=sum(1 for it in items if it.status == "exists"),
        rejected=sum(1 for it in items if it.status in ("not_vacancy", "error")),
        ai_enabled=result.ai_enabled,
        warning=result.warning,
    )


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
    user: UserRow | None = Depends(get_optional_user),
) -> VacancyListResponse:
    if resume_id is not None:
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        resume = repo.get_resume(db, resume_id)
        if resume is None or resume.user_id != user.id:
            raise HTTPException(status_code=404, detail="Resume not found")
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


@router.get("/{vacancy_id}", response_model=Vacancy)
def get_vacancy(vacancy_id: str, db: Session = Depends(get_db)) -> Vacancy:
    """Полная вакансия (с raw/cleaned_text) для карточки/модалки."""
    vacancy = repo.get_vacancy(db, vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return vacancy
