"""Маппинг Vacancy <-> VacancyRow."""

from __future__ import annotations

import uuid

from tg_jobs_parser.models.vacancy import Vacancy

from backend.db.models import VacancyRow


def vacancy_to_row(v: Vacancy, *, parse_run_id: uuid.UUID | None = None) -> VacancyRow:
    return VacancyRow(
        id=v.id,
        source_channel=v.source_channel,
        source_url=v.source_url,
        post_url=v.post_url,
        post_date=v.post_date,
        raw_text=v.raw_text,
        cleaned_text=v.cleaned_text,
        detected_specializations=list(v.detected_specializations),
        detected_grade=v.detected_grade,
        detected_salary_min=v.detected_salary_min,
        detected_salary_max=v.detected_salary_max,
        detected_salary_avg=v.detected_salary_avg,
        salary_currency=v.salary_currency,
        work_format=v.work_format,
        country=v.country,
        city=v.city,
        company=v.company,
        contact=v.contact,
        technologies=list(v.technologies),
        required_experience=v.required_experience,
        employment_type=v.employment_type,
        application_urls=list(v.application_urls),
        extraction_source=v.extraction_source,
        ai_confidence=v.ai_confidence,
        vacancy_score=v.vacancy_score,
        vacancy_substantive_score=v.vacancy_substantive_score,
        is_duplicate=v.is_duplicate,
        duplicate_group_id=v.duplicate_group_id,
        duplicate_reason=v.duplicate_reason,
        parsed_at=v.parsed_at,
        last_parse_run_id=parse_run_id,
    )


def row_to_vacancy(row: VacancyRow) -> Vacancy:
    return Vacancy(
        id=row.id,
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
        vacancy_score=row.vacancy_score,
        vacancy_substantive_score=row.vacancy_substantive_score,
        is_duplicate=row.is_duplicate,
        duplicate_group_id=row.duplicate_group_id,
        duplicate_reason=row.duplicate_reason,
        parsed_at=row.parsed_at,
    )
