"""Запуск parse job: enqueue (RQ) или in-process thread."""

from __future__ import annotations

import logging
import threading
import uuid

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from backend.config_builder import config_snapshot_dict
from backend.db import repository as repo
from backend.db.session import session_scope
from backend.schemas import RunCreateRequest
from backend.services.parse_run_events import publish_parse_run_event
from backend.settings import get_settings
from backend.workers.parse_tasks import run_parse_job
from tg_jobs_parser.config.schema import AppConfig

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)


def _current_traceparent() -> str | None:
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        carrier: dict[str, str] = {}
        TraceContextTextMapPropagator().inject(carrier)
        return carrier.get("traceparent")
    except Exception:
        return None


def start_parse_run(config: AppConfig, _body: RunCreateRequest) -> uuid.UUID:
    with session_scope() as session:
        if repo.has_active_parse_run(session):
            raise RuntimeError("Уже выполняется прогон парсинга. Дождитесь завершения или отмените его.")
        snapshot = config_snapshot_dict(config)
        row = repo.create_parse_run(session, config_snapshot=snapshot, status="queued")
        run_id = row.id

    settings = get_settings()
    backend = settings.parse_job_backend_normalized
    traceparent = _current_traceparent()

    with _TRACER.start_as_current_span(
        "parse_run.enqueue",
        kind=SpanKind.PRODUCER,
        attributes={
            "parse.run_id": str(run_id),
            "parse.backend": backend,
            "messaging.system": "rq" if backend == "rq" else "inprocess",
            "messaging.destination": "parse",
        },
    ):
        try:
            if backend == "rq":
                from backend.queue.rq_conn import get_parse_queue

                q = get_parse_queue()
                rq_job = q.enqueue(
                    run_parse_job,
                    str(run_id),
                    traceparent,
                    job_timeout=settings.parse_rq_job_timeout_seconds,
                    result_ttl=3600,
                    failure_ttl=86400,
                )
                with session_scope() as session:
                    repo.set_parse_run_rq_job_id(session, run_id, str(rq_job.id))
                _LOG.info("Прогон %s поставлен в RQ parse (job=%s)", run_id, rq_job.id)
            else:
                threading.Thread(
                    target=run_parse_job,
                    args=(str(run_id), traceparent),
                    name=f"parse-run-{run_id}",
                    daemon=True,
                ).start()
                _LOG.info("Прогон %s запущен inprocess", run_id)
        except Exception:
            with session_scope() as session:
                repo.set_run_status(
                    session,
                    run_id,
                    "failed",
                    error_message="Failed to enqueue job",
                )
            raise

    return run_id


def cancel_run(run_id: uuid.UUID) -> bool:
    """Durable cancel: queued → cancelled; running → cancel_requested (worker polls)."""
    with session_scope() as session:
        row = repo.request_parse_run_cancel(session, run_id)
        if row is None:
            return False
        rq_job_id = row.rq_job_id
        status = row.status
    if status == "cancelled":
        if rq_job_id:
            try:
                from rq.job import Job

                from backend.queue.rq_conn import get_redis_connection

                Job.fetch(rq_job_id, connection=get_redis_connection()).cancel()
            except Exception:
                pass
        publish_parse_run_event(run_id, event="done", payload={})
    _LOG.info("Прогон %s: запрошена отмена (status=%s)", run_id, status)
    return True
