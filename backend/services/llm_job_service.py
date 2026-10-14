"""Очередь фоновых LLM-джоб: in-process ThreadPool или Redis Queue (RQ)."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_llm_job, record_llm_job_enqueue
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.utils.thread_pool import DaemonThreadPoolExecutor

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)

KIND_MATCH = "match"
KIND_COVER_LETTER = "cover_letter"


class LlmJobUserLimitError(RuntimeError):
    """Пользователь исчерпал лимит параллельных LLM-джоб."""


class LlmDailyQuotaError(RuntimeError):
    """Пользователь исчерпал суточную квоту вызовов LLM (``LLM_USER_DAILY_CALLS``)."""


def reserve_user_llm_calls(session, *, user_id: int, calls: int) -> None:
    """Списать вызовы из суточной квоты пользователя; админ и квота 0 — без ограничений.

    Квота защищает ключ OpenRouter владельца от остальных зарегистрированных
    пользователей. Списание — в транзакции вызывающего: откат запроса возвращает квоту.
    """
    from backend.db.llm_usage_repository import try_reserve_llm_calls
    from backend.db.models import UserRow

    limit = get_settings().llm_user_daily_calls
    if limit <= 0:
        return
    user = session.get(UserRow, user_id)
    if user is not None and user.is_admin:
        return
    if not try_reserve_llm_calls(session, user_id=user_id, calls=calls, daily_limit=limit):
        raise LlmDailyQuotaError(f"Суточная квота LLM исчерпана ({limit} вызовов в сутки, сброс в 00:00 UTC).")


@dataclass(frozen=True)
class EnqueueResult:
    job_id: uuid.UUID
    reused: bool


def _current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


class LlmJobRunner:
    """Постановка LLM-задач: ``inprocess`` или ``rq``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pool: DaemonThreadPoolExecutor | None = None
        self._max_workers: int | None = None

    def reset(self) -> None:
        """Остановить пул (тесты / shutdown)."""
        with self._lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
            self._max_workers = None

    def _ensure_pool(self, max_workers: int) -> DaemonThreadPoolExecutor:
        with self._lock:
            if self._pool is None or self._max_workers != max_workers:
                if self._pool is not None:
                    self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = DaemonThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="llm-job",
                )
                self._max_workers = max_workers
            return self._pool

    def enqueue(
        self,
        *,
        user_id: int,
        kind: str,
        resume_id: int,
        vacancy_id: str,
        force: bool = False,
        params: dict | None = None,
    ) -> EnqueueResult:
        settings = get_settings()
        backend = settings.llm_job_backend_normalized
        cfg = load_config(settings.config_path)
        per_user = cfg.ai.interactive_per_user_limit
        workers = cfg.ai.interactive_concurrency

        with _TRACER.start_as_current_span(
            "llm_job.enqueue",
            kind=SpanKind.PRODUCER,
            attributes={
                "llm_job.kind": kind,
                "llm_job.backend": backend,
                "messaging.system": "rq" if backend == "rq" else "inprocess",
                "messaging.destination": "llm",
            },
        ) as span:
            with session_scope() as session:
                existing = repo.find_active_llm_job(
                    session,
                    user_id=user_id,
                    kind=kind,
                    resume_id=resume_id,
                    vacancy_id=vacancy_id,
                )
                if existing is not None:
                    span.set_attribute("llm_job.id", str(existing.id))
                    record_llm_job_enqueue(backend=backend, kind=kind, outcome="reused")
                    return EnqueueResult(job_id=existing.id, reused=True)

                active = repo.count_active_llm_jobs(session, user_id=user_id)
                if active >= per_user:
                    record_llm_job_enqueue(backend=backend, kind=kind, outcome="rejected_limit")
                    raise LlmJobUserLimitError(
                        f"Слишком много параллельных LLM-задач (лимит {per_user}). Дождитесь завершения."
                    )
                try:
                    reserve_user_llm_calls(session, user_id=user_id, calls=1)
                except LlmDailyQuotaError:
                    record_llm_job_enqueue(backend=backend, kind=kind, outcome="rejected_quota")
                    raise

                row = repo.create_llm_job(
                    session,
                    user_id=user_id,
                    kind=kind,
                    resume_id=resume_id,
                    vacancy_id=vacancy_id,
                    force=force,
                    params=params,
                )
                job_id = row.id

            span.set_attribute("llm_job.id", str(job_id))
            record_llm_job(outcome="queued", kind=kind)
            try:
                self._dispatch(job_id, backend=backend, pool_workers=workers)
            except Exception:
                record_llm_job_enqueue(backend=backend, kind=kind, outcome="error")
                with session_scope() as session:
                    repo.set_llm_job_status(
                        session,
                        job_id,
                        "error",
                        error_message="Failed to enqueue job",
                    )
                raise
            record_llm_job_enqueue(backend=backend, kind=kind, outcome="queued")
            _LOG.info(
                "LLM job queued job_id=%s kind=%s backend=%s",
                job_id,
                kind,
                backend,
            )
            return EnqueueResult(job_id=job_id, reused=False)

    def _dispatch(self, job_id: uuid.UUID, *, backend: str, pool_workers: int) -> None:
        from backend.workers.llm_tasks import run_llm_job

        traceparent = _current_traceparent()
        if backend == "rq":
            from backend.queue.rq_conn import get_llm_queue
            from backend.settings import get_settings

            timeout = get_settings().llm_rq_job_timeout_seconds
            q = get_llm_queue()
            rq_job = q.enqueue(
                run_llm_job,
                str(job_id),
                traceparent,
                job_timeout=timeout,
                result_ttl=3600,
                failure_ttl=86400,
            )
            with session_scope() as session:
                repo.set_llm_job_rq_job_id(session, job_id, str(rq_job.id))
            _LOG.info("RQ enqueue job_id=%s rq_job_id=%s", job_id, rq_job.id)
            return

        pool = self._ensure_pool(pool_workers)
        pool.submit(run_llm_job, str(job_id), traceparent)


llm_job_runner = LlmJobRunner()
