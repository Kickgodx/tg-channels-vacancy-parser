"""LLM-оценка соответствия резюме одной вакансии."""

from __future__ import annotations

import logging

from backend.db import repository as repo
from backend.settings import get_settings
from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)


def match_single(
    session, *, resume_text: str, vacancy_id: str, vacancy_text: str, resume_id: int
):
    """Синхронная оценка одной пары: вызов LLM + запись в ``vacancy_matches``."""
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
    _LOG.info(
        "Матчинг резюме %s ↔ вакансия %s: score=%s, модель %s",
        resume_id,
        vacancy_id,
        extraction.score,
        usage.model,
    )
    return repo.upsert_vacancy_match(
        session,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        score=extraction.score,
        explanation=extraction.explanation,
        matching_skills=extraction.matching_skills,
        missing_skills=extraction.missing_skills,
        model=usage.model,
    )
