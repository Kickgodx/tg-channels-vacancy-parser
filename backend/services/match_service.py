"""LLM-оценка соответствия резюме одной вакансии."""

from __future__ import annotations

import logging

from backend.db import repository as repo
from backend.settings import get_settings
from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.ai.schema import calculate_overall_score
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)


def match_single(
    session, *, resume_text: str, vacancy_id: str, vacancy_text: str, resume_id: int
):
    """Синхронная оценка одной пары: вызов LLM + запись в ``vacancy_matches``.

    ``overall_score`` считается здесь (детерминированно), LLM отдаёт только
    breakdown-оценки и объяснение.
    """
    cfg = load_config(get_settings().config_path)
    max_chars = cfg.ai.max_input_chars
    client = OpenRouterClient(cfg.ai)
    try:
        extraction, usage = client.match_with_fallback(
            resume_text=resume_text[:max_chars],
            vacancy_text=vacancy_text[:max_chars],
        )
    finally:
        client.close()
    overall_score = calculate_overall_score(
        extraction.breakdown, extraction.missing_skills
    )
    _LOG.info(
        "Матчинг резюме %s ↔ вакансия %s: score=%s (confidence=%s), модель %s",
        resume_id,
        vacancy_id,
        overall_score,
        extraction.confidence,
        usage.model,
    )
    return repo.upsert_vacancy_match(
        session,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        overall_score=overall_score,
        confidence=extraction.confidence,
        breakdown=extraction.breakdown.model_dump(),
        explanation=extraction.explanation,
        matching_skills=[s.model_dump() for s in extraction.matching_skills],
        missing_skills=[s.model_dump() for s in extraction.missing_skills],
        strengths=extraction.strengths,
        risks=extraction.risks,
        model=usage.model,
    )
