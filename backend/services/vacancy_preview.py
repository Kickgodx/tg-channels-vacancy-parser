"""Превью вакансий для live-progress / SSE (без зависимости от API routes)."""

from __future__ import annotations

from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_progress import PROGRESS_PREVIEW_LIMIT


def build_vacancies_preview(vacancies: list[Vacancy] | None, *, limit: int = PROGRESS_PREVIEW_LIMIT) -> list[dict]:
    """Последние ``limit`` вакансий с уникальным ``id`` (для React keys в live-таблице)."""
    by_id: dict[str, Vacancy] = {}
    order: list[str] = []
    for v in vacancies or []:
        if v.id not in by_id:
            order.append(v.id)
        by_id[v.id] = v
    selected = order[-limit:]
    return [
        {
            "id": v.id,
            "source_channel": v.source_channel,
            "post_url": v.post_url,
            "post_date": v.post_date.isoformat() if v.post_date else None,
            "work_format": v.work_format,
            "detected_salary_avg": v.detected_salary_avg,
            "salary_currency": v.salary_currency,
            "detected_specializations": v.detected_specializations,
            "cleaned_text": (v.cleaned_text or "")[:300],
        }
        for vid in selected
        for v in (by_id[vid],)
    ]
