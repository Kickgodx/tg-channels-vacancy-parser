"""Маппинг Vacancy <-> VacancyRow."""

from __future__ import annotations

import uuid

from sqlalchemy import case, func

from backend.db.models import VacancyRow
from backend.schemas import VacancyListItem
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.company import company_key

# Длина preview в списке (как в UI-таблице до оптимизации).
VACANCY_PREVIEW_LEN = 120


def vacancy_preview_expr():
    """SQL-выражение: cleaned_text, обрезанный до VACANCY_PREVIEW_LEN (+ «…»)."""
    text = VacancyRow.cleaned_text
    return case(
        (
            func.length(text) > VACANCY_PREVIEW_LEN,
            func.concat(func.substr(text, 1, VACANCY_PREVIEW_LEN), "…"),
        ),
        else_=text,
    ).label("preview")


def _clip(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def vacancy_to_row(v: Vacancy, *, parse_run_id: uuid.UUID | None = None) -> VacancyRow:
    return VacancyRow(
        id=_clip(v.id, 128) or v.id[:128],
        source_post_key=_clip(v.source_post_key, 128) or "",
        source_channel=_clip(v.source_channel, 128) or "",
        source_url=v.source_url,
        post_url=v.post_url,
        post_date=v.post_date,
        raw_text=v.raw_text,
        cleaned_text=v.cleaned_text,
        detected_specializations=list(v.detected_specializations),
        detected_grade=_clip(v.detected_grade, 32) or "unknown",
        detected_salary_min=v.detected_salary_min,
        detected_salary_max=v.detected_salary_max,
        detected_salary_avg=v.detected_salary_avg,
        detected_salary_avg_rub=v.detected_salary_avg_rub,
        salary_currency=_clip(v.salary_currency, 16),
        work_format=_clip(v.work_format, 32),
        country=_clip(v.country, 256),
        city=_clip(v.city, 256),
        company=_clip(v.company, 256),
        company_key=company_key(v.company),
        contact=_clip(v.contact, 512),
        technologies=list(v.technologies),
        required_experience=v.required_experience,
        employment_type=_clip(v.employment_type, 256),
        application_urls=list(v.application_urls),
        extraction_source=v.extraction_source,
        ai_confidence=v.ai_confidence,
        ai_notes=v.ai_notes,
        vacancy_score=v.vacancy_score,
        vacancy_substantive_score=v.vacancy_substantive_score,
        is_duplicate=v.is_duplicate,
        duplicate_group_id=_clip(v.duplicate_group_id, 128),
        duplicate_reason=v.duplicate_reason,
        parsed_at=v.parsed_at,
        last_parse_run_id=parse_run_id,
        archived_at=v.archived_at,
    )


def row_to_vacancy(row: VacancyRow) -> Vacancy:
    return Vacancy(
        id=row.id,
        source_post_key=row.source_post_key,
        source_channel=row.source_channel,
        source_url=row.source_url,
        post_url=row.post_url,
        post_date=row.post_date,
        raw_text=row.raw_text,
        cleaned_text=row.cleaned_text,
        detected_specializations=list(row.detected_specializations or []),
        detected_grade=row.detected_grade,
        detected_salary_min=row.detected_salary_min,
        detected_salary_max=row.detected_salary_max,
        detected_salary_avg=row.detected_salary_avg,
        detected_salary_avg_rub=row.detected_salary_avg_rub,
        salary_currency=row.salary_currency,
        work_format=row.work_format,
        country=row.country,
        city=row.city,
        company=row.company,
        contact=row.contact,
        technologies=list(row.technologies or []),
        required_experience=row.required_experience,
        employment_type=row.employment_type,
        application_urls=list(row.application_urls or []),
        extraction_source=row.extraction_source,
        ai_confidence=row.ai_confidence,
        ai_notes=row.ai_notes,
        vacancy_score=row.vacancy_score,
        vacancy_substantive_score=row.vacancy_substantive_score,
        is_duplicate=row.is_duplicate,
        duplicate_group_id=row.duplicate_group_id,
        duplicate_reason=row.duplicate_reason,
        parsed_at=row.parsed_at,
        archived_at=row.archived_at,
    )


def row_to_list_item(
    row: VacancyRow,
    preview: str,
    *,
    match_score: float | None = None,
    match_explanation: str | None = None,
    match_stale: bool | None = None,
) -> VacancyListItem:
    return VacancyListItem(
        id=row.id,
        source_post_key=row.source_post_key,
        source_channel=row.source_channel,
        source_url=row.source_url,
        post_url=row.post_url,
        post_date=row.post_date,
        preview=preview or "",
        detected_specializations=list(row.detected_specializations or []),
        detected_grade=row.detected_grade,
        detected_salary_min=row.detected_salary_min,
        detected_salary_max=row.detected_salary_max,
        detected_salary_avg=row.detected_salary_avg,
        detected_salary_avg_rub=row.detected_salary_avg_rub,
        salary_currency=row.salary_currency,
        work_format=row.work_format,
        country=row.country,
        city=row.city,
        company=row.company,
        contact=row.contact,
        technologies=list(row.technologies or []),
        required_experience=row.required_experience,
        employment_type=row.employment_type,
        application_urls=list(row.application_urls or []),
        extraction_source=row.extraction_source,
        ai_confidence=row.ai_confidence,
        ai_notes=row.ai_notes,
        vacancy_score=row.vacancy_score,
        vacancy_substantive_score=row.vacancy_substantive_score,
        is_duplicate=row.is_duplicate,
        duplicate_group_id=row.duplicate_group_id,
        duplicate_reason=row.duplicate_reason,
        match_score=match_score,
        match_explanation=match_explanation,
        match_stale=match_stale,
        parsed_at=row.parsed_at,
        archived_at=row.archived_at,
    )
