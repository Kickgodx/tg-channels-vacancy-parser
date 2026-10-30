"""Переразбор LLM уже сохранённых вакансий (LLM-джоба ``reextract``, только admin).

Нужен после смены правил извлечения, которую не исправить SQL: миграция 037 вынесла
языки из специализаций в технологии, и вакансии вида ``["Go"]`` остались без роли —
роль по тексту может назначить только модель. Цели (id вакансий) фиксируются при
постановке джобы, дальше — как импорт (``import_service.run_import_job``): вакансия за
вакансией, вызов LLM вне транзакции, итог по каждой — в ``llm_jobs.result`` (fenced по
lease), ретрай продолжает с первой необработанной, отмена — перед каждой вакансией.

Ответ «не вакансия» строку не удаляет (статус ``kept``): решение о публикации уже
принималось при прогоне, а переразбор правит только поля.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db import llm_job_repository as llm_job_repo
from backend.db import vacancy_repository as vacancy_repo
from backend.db.llm_usage_repository import refund_llm_calls
from backend.db.models import VacancyRow
from backend.db.session import session_scope
from backend.db.vacancy_mapper import row_to_vacancy
from backend.schemas import VacancyReextractItem, VacancyReextractResponse
from backend.services.import_service import enrich_with_status
from backend.services.llm_job_events import publish_llm_job_event
from backend.services.llm_job_service import (
    KIND_REEXTRACT,
    LlmDailyQuotaError,
    LlmJobCancelled,
    reserve_user_llm_calls,
)
from backend.settings import get_settings
from jobdesk.config.loader import load_config
from jobdesk.hh.mapper import HH_CHANNEL

_LOG = logging.getLogger(__name__)

# Потолок одной джобы: столько вызовов LLM подряд; остальное — следующей джобой.
MAX_REEXTRACT_VACANCIES = 500


def _missing_role_conditions():
    # Telegram и ручной импорт: у HH роль — из professional_roles, LLM их не разбирает.
    return (
        VacancyRow.archived_at.is_(None),
        VacancyRow.is_duplicate.is_(False),
        VacancyRow.source_channel != HH_CHANNEL,
        func.jsonb_array_length(VacancyRow.detected_specializations) == 0,
    )


def count_missing_role(session: Session) -> int:
    """Сколько активных вакансий (не HH) без специализации."""
    return int(session.scalar(select(func.count(VacancyRow.id)).where(*_missing_role_conditions())) or 0)


def missing_role_vacancy_ids(session: Session, *, limit: int = MAX_REEXTRACT_VACANCIES) -> list[str]:
    """Цели переразбора: активные вакансии не из HH без специализации, свежие первыми."""
    stmt = (
        select(VacancyRow.id)
        .where(*_missing_role_conditions())
        .order_by(VacancyRow.post_date.desc().nulls_last(), VacancyRow.id)
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def job_params(vacancy_ids: list[str]) -> dict:
    return {"vacancy_ids": vacancy_ids}


def _item(index: int, vacancy_id: str, status: str, message: str | None = None) -> dict:
    return {"index": index, "vacancy_id": vacancy_id, "status": status, "message": message}


def run_reextract_job(
    job_id: uuid.UUID,
    *,
    user_id: int,
    params: dict,
    result: dict | None,
    lease_owner: str,
    is_cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Выполнить джобу переразбора (LLM-воркер). False — lease потерян, итог не дописан."""
    ids: list[str] = [str(v) for v in params.get("vacancy_ids") or []]
    config = load_config(get_settings().config_path)
    state = dict(result or {})
    items: list[dict] = list(state.get("items") or [])
    done = {it["index"] for it in items}
    warning: str | None = state.get("warning")

    if not config.ai.enabled:
        raise RuntimeError("AI выключен — переразбор вакансий невозможен.")
    from jobdesk.ai.enricher import AIEnrichmentService, LlmUnavailableError

    # Как в импорте: score-гейт перед LLM не нужен — вакансии уже прошли прогон.
    ingest_config = config.model_copy(update={"ai": config.ai.model_copy(update={"skip_if_vacancy_score_below": 0})})
    try:
        ai_service = AIEnrichmentService(ingest_config)
    except Exception as e:
        raise LlmUnavailableError(f"LLM не запущен: {e}. Исправьте конфигурацию LLM (ключ, модель).") from e
    ai_service.begin_batch(len(ids) - len(done))

    def _commit(item: dict, vacancy=None) -> bool:
        payload = {"total": len(ids), "warning": warning, "items": [*items, item]}
        with session_scope() as session:
            if not llm_job_repo.patch_llm_job_result(session, job_id, payload, lease_owner=lease_owner):
                return False
            if vacancy is not None:
                vacancy_repo.upsert_vacancies(session, [vacancy], parse_run_id=None)
        items.append(item)
        publish_llm_job_event(job_id, status="running", kind=KIND_REEXTRACT)
        return True

    try:
        for i, vid in enumerate(ids):
            if i in done:
                continue
            if is_cancelled is not None and is_cancelled():
                _LOG.info("Переразбор %s: отменён, обработано %s из %s", job_id, len(items), len(ids))
                raise LlmJobCancelled("Переразбор отменён")
            with session_scope() as session:
                row = session.get(VacancyRow, vid)
                vacancy = row_to_vacancy(row) if row is not None else None
            if vacancy is None:
                if not _commit(_item(i, vid, "error", "Вакансия удалена")):
                    return False
                continue

            try:
                with session_scope() as session:
                    quota_day = reserve_user_llm_calls(session, user_id=user_id, calls=1)
            except LlmDailyQuotaError as e:
                # Остальные вакансии без LLM не исправить — останавливаемся с итогом.
                warning = f"{e} Переразбор остановлен."
                _LOG.warning("Переразбор %s: %s", job_id, e)
                with session_scope() as session:
                    payload = {"total": len(ids), "warning": warning, "items": items}
                    if not llm_job_repo.patch_llm_job_result(session, job_id, payload, lease_owner=lease_owner):
                        return False
                break

            calls_before = ai_service.stats.calls
            enriched, reject_status, message = enrich_with_status(
                ai_service,
                vacancy,
                ingest_config,
                skip_below=0,
            )
            if quota_day is not None and ai_service.stats.calls == calls_before:
                with session_scope() as session:
                    refund_llm_calls(session, user_id=user_id, day=quota_day)
            if reject_status == "not_vacancy":
                ok = _commit(_item(i, vid, "kept", "LLM: не похоже на вакансию — строка не изменена"))
            elif reject_status is not None or enriched is None:
                ok = _commit(_item(i, vid, "error", message))
            elif message:
                # Ошибка LLM в assist-режиме: enrich вернул rules-разбор — роль он не исправит.
                ok = _commit(_item(i, vid, "error", message))
            else:
                ok = _commit(_item(i, vid, "updated"), enriched)
            if not ok:
                return False
    finally:
        ai_service.log_batch_finished()
        ai_service.close()

    _LOG.info("Переразбор %s завершён: %s из %s", job_id, len(items), len(ids))
    return True


def reextract_response(data: dict | None) -> VacancyReextractResponse:
    """Итог джобы (в т.ч. частичный, пока она выполняется)."""
    data = data or {}
    items = [VacancyReextractItem(**it) for it in sorted(data.get("items") or [], key=lambda it: it["index"])]
    return VacancyReextractResponse(
        total=int(data.get("total") or 0),
        items=items,
        updated=sum(1 for it in items if it.status == "updated"),
        kept=sum(1 for it in items if it.status == "kept"),
        errors=sum(1 for it in items if it.status == "error"),
        warning=data.get("warning"),
    )
