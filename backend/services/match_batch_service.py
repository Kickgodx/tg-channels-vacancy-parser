"""Пакетная LLM-оценка резюме по нескольким вакансиям (LLM-джоба ``match_batch``).

Кнопка «Оценить топ-N» в подборе под резюме: вакансии фиксируются при постановке, дальше —
как переразбор: по одной, вызов LLM вне транзакции, итог по каждой — в ``llm_jobs.result``
(fenced по lease), ретрай продолжает с первой необработанной, отмена — перед каждой.
Свежая оценка пары не пересчитывается (``skipped``), квота списывается за каждую оценённую.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from backend.db import llm_job_repository as llm_job_repo
from backend.db import repository as repo
from backend.db.llm_usage_repository import refund_llm_calls
from backend.db.models import ResumeRow, VacancyRow
from backend.db.session import session_scope
from backend.schemas import MatchBatchItem, MatchBatchResponse
from backend.services import match_service
from backend.services.llm_job_events import publish_llm_job_event
from backend.services.llm_job_service import (
    KIND_MATCH_BATCH,
    LlmDailyQuotaError,
    LlmJobCancelled,
    reserve_user_llm_calls,
)
from backend.services.llm_result_freshness import current_ai_config, match_is_stale
from tg_jobs_parser.ai.rate_limit import LlmQuotaExhausted

_LOG = logging.getLogger(__name__)

MAX_BATCH_MATCH = 20


def job_params(resume_id: int, vacancy_ids: list[str]) -> dict:
    return {"resume_id": resume_id, "vacancy_ids": vacancy_ids}


def _item(index: int, vacancy_id: str, status: str, *, score: int | None = None, message: str | None = None) -> dict:
    return {"index": index, "vacancy_id": vacancy_id, "status": status, "score": score, "message": message}


def run_match_batch_job(
    job_id: uuid.UUID,
    *,
    user_id: int,
    params: dict,
    result: dict | None,
    lease_owner: str,
    is_cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Выполнить пакетную оценку (LLM-воркер). False — lease потерян, итог не дописан."""
    resume_id = int(params["resume_id"])
    ids: list[str] = [str(v) for v in params.get("vacancy_ids") or []]
    state = dict(result or {})
    items: list[dict] = list(state.get("items") or [])
    done = {it["index"] for it in items}
    warning: str | None = state.get("warning")

    with session_scope() as session:
        resume = session.get(ResumeRow, resume_id)
        if resume is None:
            raise RuntimeError("Резюме удалено")
        resume_text = resume.text

    def _save(new_items: list[dict]) -> bool:
        payload = {"total": len(ids), "warning": warning, "items": new_items}
        with session_scope() as session:
            if not llm_job_repo.patch_llm_job_result(session, job_id, payload, lease_owner=lease_owner):
                return False
        publish_llm_job_event(job_id, status="running", kind=KIND_MATCH_BATCH)
        return True

    def _commit(item: dict) -> bool:
        if not _save([*items, item]):
            return False
        items.append(item)
        return True

    ai = current_ai_config()
    for i, vid in enumerate(ids):
        if i in done:
            continue
        if is_cancelled is not None and is_cancelled():
            _LOG.info("Пакетная оценка %s: отменена, обработано %s из %s", job_id, len(items), len(ids))
            raise LlmJobCancelled("Пакетная оценка отменена")
        with session_scope() as session:
            vacancy = session.get(VacancyRow, vid)
            vacancy_text = (vacancy.cleaned_text or vacancy.raw_text or "") if vacancy is not None else None
            existing = repo.get_vacancy_match(session, resume_id=resume_id, vacancy_id=vid)
            fresh_score = existing.overall_score if existing is not None and not match_is_stale(existing, ai) else None
        if vacancy_text is None:
            ok = _commit(_item(i, vid, "error", message="Вакансия удалена"))
        elif fresh_score is not None:
            ok = _commit(_item(i, vid, "skipped", score=fresh_score))
        else:
            try:
                with session_scope() as session:
                    quota_day = reserve_user_llm_calls(session, user_id=user_id, calls=1)
            except LlmDailyQuotaError as e:
                warning = f"{e} Оценка остановлена."
                if not _save(items):
                    return False
                break
            try:
                match_service.match_single(
                    resume_id=resume_id, resume_text=resume_text, vacancy_id=vid, vacancy_text=vacancy_text
                )
                # match_single возвращает строку закрытой сессии — счёт читаем заново.
                with session_scope() as session:
                    saved = repo.get_vacancy_match(session, resume_id=resume_id, vacancy_id=vid)
                    score = saved.overall_score if saved is not None else None
                ok = _commit(_item(i, vid, "scored", score=score))
            except LlmQuotaExhausted as e:
                # Общий суточный бюджет или лимит провайдера: остальные упрутся в то же.
                if quota_day is not None:
                    with session_scope() as session:
                        refund_llm_calls(session, user_id=user_id, day=quota_day)
                warning = f"LLM недоступен: {e}. Оценка остановлена."
                if not _save(items):
                    return False
                break
            except Exception as e:
                _LOG.warning("Пакетная оценка %s: вакансия %s: %s", job_id, vid, e)
                if quota_day is not None:
                    with session_scope() as session:
                        refund_llm_calls(session, user_id=user_id, day=quota_day)
                ok = _commit(_item(i, vid, "error", message="Модель не ответила — попробуйте позже"))
        if not ok:
            return False
    _LOG.info("Пакетная оценка %s завершена: %s из %s", job_id, len(items), len(ids))
    return True


def match_batch_response(data: dict | None) -> MatchBatchResponse:
    data = data or {}
    items = [MatchBatchItem(**it) for it in sorted(data.get("items") or [], key=lambda it: it["index"])]
    return MatchBatchResponse(
        total=int(data.get("total") or 0),
        items=items,
        scored=sum(1 for it in items if it.status == "scored"),
        skipped=sum(1 for it in items if it.status == "skipped"),
        errors=sum(1 for it in items if it.status == "error"),
        warning=data.get("warning"),
    )
