"""Сборка VacancyQuery из HTTP query-параметров (общие фильтры списка и агрегатов)."""

from __future__ import annotations

from datetime import date as date_type

from backend.schemas import VacancyQuery, VacancySortField, VacancySortOrder


def vacancy_query_from_params(
    *,
    page: int = 1,
    page_size: int = 50,
    source_channel: str | None = None,
    grade: str | None = None,
    specialization: list[str] | None = None,
    work_format: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    has_salary: bool | None = None,
    q: str | None = None,
    include_archived: bool = False,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
) -> VacancyQuery:
    df = date_type.fromisoformat(date_from) if date_from else None
    dt = date_type.fromisoformat(date_to) if date_to else None
    return VacancyQuery(
        page=page,
        page_size=page_size,
        source_channel=source_channel,
        grade=grade,
        specializations=specialization or [],
        work_format=work_format,
        date_from=df,
        date_to=dt,
        has_salary=has_salary,
        q=q,
        include_archived=include_archived,
        sort_by=sort_by,
        sort_order=sort_order,
    )
