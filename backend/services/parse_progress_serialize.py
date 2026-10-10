"""SSE-совместимая сериализация ParseProgressSnapshot."""

from __future__ import annotations

from backend.services.vacancy_preview import build_vacancies_preview
from tg_jobs_parser.parse_progress import ParseProgressSnapshot


def snapshot_to_progress_dict(snap: ParseProgressSnapshot) -> dict:
    return {
        "posts_processed": snap.posts_processed,
        "posts_total": snap.posts_total,
        "vacancies_found": snap.vacancies_found,
        "phase": snap.phase,
        "ai_llm_disabled": snap.ai_llm_disabled,
        "ai_llm_disabled_reason": snap.ai_llm_disabled_reason,
        "ai_tokens_in": snap.ai_tokens_in,
        "ai_tokens_out": snap.ai_tokens_out,
        "ai_skipped_score": snap.ai_skipped_score,
        "vacancies_preview": build_vacancies_preview(snap.vacancies),
    }
