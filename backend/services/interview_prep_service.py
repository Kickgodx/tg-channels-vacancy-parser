"""LLM-подготовка к собеседованию: вероятные вопросы по вакансии и тезисы ответов из резюме."""

from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import trace

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.services.interactive_llm import get_interactive_client
from backend.services.llm_job_service import LlmJobCancelled
from backend.services.resume_tailoring_service import saved_match_skills
from backend.settings import get_settings
from jobdesk.ai.prompts import INTERVIEW_PREP_PROMPT_VERSION
from jobdesk.config.loader import load_config

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)


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
        "interview_prep.generate", attributes={"resume.id": resume_id, "vacancy.id": vacancy_id}
    ) as span:
        cfg = load_config(get_settings().config_path)
        max_chars = cfg.ai.max_input_chars
        requested_model = cfg.ai.resolved_cover_letter_model()
        # Недостающие навыки сохранённой оценки — темы «gap»-вопросов, а не опыт кандидата.
        match_summary = saved_match_skills(resume_id=resume_id, vacancy_id=vacancy_id)
        span.set_attribute("interview_prep.has_match_summary", match_summary is not None)
        result, usage = get_interactive_client().interview_prep_with_fallback(
            resume_text=resume_text[:max_chars],
            vacancy_text=vacancy_text[:max_chars],
            match_summary=match_summary,
        )
        span.set_attribute("interview_prep.questions", len(result.questions))
        span.set_attribute("gen_ai.response.model", usage.model)
    _LOG.info(
        "Подготовка к собеседованию: резюме %s ↔ вакансия %s: %s вопросов, модель %s",
        resume_id,
        vacancy_id,
        len(result.questions),
        usage.model,
    )
    if is_cancelled is not None and is_cancelled():
        raise LlmJobCancelled("Подготовка отменена — ответ модели не сохранён", model_answered=True)
    with session_scope() as session:
        return repo.upsert_interview_prep(
            session,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            summary=result.summary,
            questions=[q.model_dump() for q in result.questions],
            gaps=[g.model_dump() for g in result.gaps],
            questions_to_ask=list(result.questions_to_ask),
            model=usage.model,
            requested_model=requested_model,
            prompt_version=INTERVIEW_PREP_PROMPT_VERSION,
        )
