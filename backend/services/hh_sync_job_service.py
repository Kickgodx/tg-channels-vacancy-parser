"""Запуск фонового HH sync: строка ``hh_sync_jobs`` + enqueue (RQ ``hh``) или поток в API."""

from __future__ import annotations

import logging
import threading
import uuid

from sqlalchemy.exc import IntegrityError

from backend.db import hh_sync_job_repository as jobs
from backend.db.session import session_scope
from backend.errors import CODE_CONFLICT_ACTIVE_RUN, AppError
from backend.services.hh_sync_events import publish_hh_sync_event
from backend.services.hh_sync_window import ResolvedHhSearchWindow
from backend.settings import get_settings
from backend.tracing import current_traceparent

_LOG = logging.getLogger(__name__)

_ACTIVE_MSG = "HH sync уже выполняется. Дождитесь его завершения."


def _conflict() -> AppError:
    return AppError(CODE_CONFLICT_ACTIVE_RUN, _ACTIVE_MSG, status_code=409, retryable=False)


def enqueue_hh_sync_job(job_id: uuid.UUID, traceparent: str | None = None) -> None:
    """Отправить ``queued``-джобу исполнителю. Бросает исключение, если очередь недоступна."""
    from backend.workers.hh_sync_tasks import run_hh_sync_job

    settings = get_settings()
    if settings.hh_sync_job_backend_normalized == "rq":
        from backend.queue.rq_conn import get_hh_queue

        rq_job = get_hh_queue().enqueue(
            run_hh_sync_job,
            str(job_id),
            traceparent,
            job_timeout=settings.hh_sync_rq_job_timeout_seconds,
            result_ttl=3600,
            failure_ttl=86400,
        )
        with session_scope() as session:
            jobs.set_hh_sync_job_rq_job_id(session, job_id, str(rq_job.id))
        _LOG.info("HH sync %s поставлен в RQ hh (job=%s)", job_id, rq_job.id)
    else:
        threading.Thread(
            target=run_hh_sync_job,
            args=(str(job_id), traceparent),
            name=f"hh-sync-{job_id}",
            daemon=True,
        ).start()
        _LOG.info("HH sync %s запущен inprocess", job_id)


def start_hh_sync_job(window: ResolvedHhSearchWindow, *, requested_by: int | None) -> uuid.UUID:
    """Создать джобу и поставить в очередь. 409, если sync уже активен."""
    params = {
        "window": window.window,
        "period": window.period,
        "date_from": window.date_from,
        "date_to": window.date_to,
    }
    with session_scope() as session:
        if jobs.has_active_hh_sync_job(session):
            raise _conflict()
        try:
            job_id = jobs.create_hh_sync_job(session, params=params, requested_by=requested_by).id
        except IntegrityError as e:
            raise _conflict() from e

    try:
        enqueue_hh_sync_job(job_id, current_traceparent())
    except Exception:
        with session_scope() as session:
            jobs.fail_hh_sync_job(session, job_id, "Failed to enqueue job")
        publish_hh_sync_event(job_id, status="failed")
        raise
    return job_id
