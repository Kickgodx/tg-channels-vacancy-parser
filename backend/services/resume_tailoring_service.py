"""LLM-адаптация резюме под вакансию: советы, как переписать реальный опыт под требования."""

from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import trace

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.services.interactive_llm import get_interactive_client
from backend.services.llm_job_service import LlmJobCancelled
from backend.settings import get_settings
from jobdesk.ai.prompts import RESUME_TAILOR_PROMPT_VERSION
from jobdesk.config.loader import load_config

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)


def _skills(items) -> list[str]:
    return [str(s.get("skill", "")) for s in items or [] if isinstance(s, dict) and s.get("skill")]


def saved_match_skills(*, resume_id: int, vacancy_id: str) -> dict | None:
    """Совпадающие и недостающие навыки из сохранённой оценки (если она есть)."""
    with session_scope() as session:
        row = repo.get_vacancy_match(session, resume_id=resume_id, vacancy_id=vacancy_id)
        if row is None:
            return None
        return {"matching_skills": _skills(row.matching_skills), "missing_skills": _skills(row.missing_skills)}


def generate_single(
    *,
    resume_id: int,
    resume_text: str,
    vacancy_id: str,
    vacancy_text: str,
    is_cancelled: Callable[[], bool] | None = None,
):
    """LLM без удержания DB-сессии, затем upsert. Отмена после ответа модели — результат не пишется."""
    with _TRACER.start_as_current_span(
        "resume_tailor.generate", attributes={"resume.id": resume_id, "vacancy.id": vacancy_id}
    ) as span:
        cfg = load_config(get_settings().config_path)
        max_chars = cfg.ai.max_input_chars
        requested_model = cfg.ai.resolved_cover_letter_model()
        match_summary = saved_match_skills(resume_id=resume_id, vacancy_id=vacancy_id)
        span.set_attribute("resume_tailor.has_match_summary", match_summary is not None)
        result, usage = get_interactive_client().tailor_resume_with_fallback(
            resume_text=resume_text[:max_chars],
            vacancy_text=vacancy_text[:max_chars],
            match_summary=match_summary,
        )
        span.set_attribute("resume_tailor.suggestions", len(result.suggestions))
        span.set_attribute("gen_ai.response.model", usage.model)
    _LOG.info(
        "Адаптация резюме %s ↔ вакансия %s: %s советов, модель %s",
        resume_id,
        vacancy_id,
        len(result.suggestions),
        usage.model,
    )
    if is_cancelled is not None and is_cancelled():
        raise LlmJobCancelled("Адаптация отменена — ответ модели не сохранён", model_answered=True)
    with session_scope() as session:
        return repo.upsert_resume_tailoring(
            session,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            summary=result.summary,
            suggestions=[s.model_dump() for s in result.suggestions],
            keywords=list(result.keywords),
            do_not_claim=list(result.do_not_claim),
            model=usage.model,
            requested_model=requested_model,
            prompt_version=RESUME_TAILOR_PROMPT_VERSION,
        )
