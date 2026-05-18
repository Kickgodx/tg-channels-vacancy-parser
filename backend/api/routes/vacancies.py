from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.db import repository as repo
from backend.schemas import VacancyListResponse, VacancyQuery, VacancySortField, VacancySortOrder

router = APIRouter(prefix="/vacancies", tags=["vacancies"], dependencies=[Depends(verify_api_key)])


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
    from datetime import date as date_type

    df = date_type.fromisoformat(date_from) if date_from else None
    dt = date_type.fromisoformat(date_to) if date_to else None
    query = VacancyQuery(
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
    items, total = repo.list_vacancies(db, query)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)
