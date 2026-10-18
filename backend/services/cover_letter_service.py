"""LLM-генерация сопроводительного письма для пары резюме ↔ вакансия."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from opentelemetry import trace

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_cover_letter
from backend.services.interactive_llm import get_interactive_client
from backend.services.llm_job_service import LlmJobCancelled
from backend.settings import get_settings
from tg_jobs_parser.ai.prompts import COVER_LETTER_PROMPT_VERSION
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)


def _match_summary(*, resume_id: int, vacancy_id: str) -> dict | None:
    """Контекст из сохранённой оценки матчинга (если уже считалась)."""
    with session_scope() as session:
        row = repo.get_vacancy_match(session, resume_id=resume_id, vacancy_id=vacancy_id)
        if row is None:
            return None
        skills = [s.get("skill", "") for s in row.matching_skills or [] if isinstance(s, dict)]
        return {
            "score": row.overall_score,
            "strengths": list(row.strengths or []),
            "matching_skills": [s for s in skills if s],
            "explanation": row.explanation or "",
        }


def generate_single(
    *,
    resume_id: int,
    resume_text: str,
    vacancy_id: str,
    vacancy_text: str,
    tone: str = "neutral",
    extra_notes: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
):
    """Генерация письма: LLM без удержания DB-сессии, затем upsert.

    ``is_cancelled`` проверяется после ответа модели: отмена → ``LlmJobCancelled``,
    прежнее письмо пары не перезаписывается.
    """
    with _TRACER.start_as_current_span(
        "cover_letter.generate",
        attributes={
            "resume.id": resume_id,
            "vacancy.id": vacancy_id,
            "cover_letter.tone": tone,
        },
    ) as span:
        cfg = load_config(get_settings().config_path)
        max_chars = cfg.ai.max_input_chars
        # С какой моделью и версией промпта спрашиваем — по ним потом решается, устарело ли письмо.
        requested_model = cfg.ai.resolved_cover_letter_model()
        match_summary = _match_summary(resume_id=resume_id, vacancy_id=vacancy_id)
        span.set_attribute("cover_letter.has_match_summary", match_summary is not None)
        started = time.perf_counter()
        try:
            client = get_interactive_client()
            text, usage = client.cover_letter_with_fallback(
                resume_text=resume_text[:max_chars],
                vacancy_text=vacancy_text[:max_chars],
                tone=tone,
                extra_notes=extra_notes,
                match_summary=match_summary,
            )
        except Exception:
            record_cover_letter(outcome="error", duration_seconds=time.perf_counter() - started)
            raise
        span.set_attribute("cover_letter.chars", len(text))
        span.set_attribute("gen_ai.response.model", usage.model)
    record_cover_letter(outcome="success", duration_seconds=time.perf_counter() - started)
    _LOG.info(
        "Письмо резюме %s ↔ вакансия %s: %s символов, тон %s, модель %s",
        resume_id,
        vacancy_id,
        len(text),
        tone,
        usage.model,
    )
    if is_cancelled is not None and is_cancelled():
        raise LlmJobCancelled("Письмо отменено — ответ модели не сохранён", model_answered=True)
    with session_scope() as session:
        return repo.upsert_cover_letter(
            session,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            text=text,
            tone=tone,
            model=usage.model,
            requested_model=requested_model,
            prompt_version=COVER_LETTER_PROMPT_VERSION,
        )
