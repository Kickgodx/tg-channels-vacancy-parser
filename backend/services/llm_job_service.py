"""Очередь фоновых LLM-джоб (мэтч / письмо) в одном процессе backend."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

from opentelemetry import trace

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_llm_job
from backend.services import cover_letter_service, match_service
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.utils.thread_pool import DaemonThreadPoolExecutor

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)

KIND_MATCH = "match"
KIND_COVER_LETTER = "cover_letter"


class LlmJobUserLimitError(RuntimeError):
    """Пользователь исчерпал лимит параллельных LLM-джоб."""


@dataclass(frozen=True)
class EnqueueResult:
    job_id: uuid.UUID
    reused: bool


class LlmJobRunner:
    """In-process пул: ``ai.interactive_concurrency`` воркеров."""

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
        cfg = load_config(get_settings().config_path)
        per_user = cfg.ai.interactive_per_user_limit
        workers = cfg.ai.interactive_concurrency

        with session_scope() as session:
            existing = repo.find_active_llm_job(
                session,
                user_id=user_id,
                kind=kind,
                resume_id=resume_id,
                vacancy_id=vacancy_id,
            )
            if existing is not None:
                return EnqueueResult(job_id=existing.id, reused=True)

            active = repo.count_active_llm_jobs(session, user_id=user_id)
            if active >= per_user:
                raise LlmJobUserLimitError(
                    f"Слишком много параллельных LLM-задач (лимит {per_user}). Дождитесь завершения."
                )

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

        record_llm_job(outcome="queued", kind=kind)
        pool = self._ensure_pool(workers)
        pool.submit(self._run_job, job_id)
        return EnqueueResult(job_id=job_id, reused=False)

    def _run_job(self, job_id: uuid.UUID) -> None:
        started = time.perf_counter()
        kind = "unknown"
        try:
            with session_scope() as session:
                job = repo.get_llm_job(session, job_id)
                if job is None or job.status != "queued":
                    return
                kind = job.kind
                resume = repo.get_resume(session, job.resume_id)
                vacancy = repo.get_vacancy_row(session, job.vacancy_id)
                if resume is None or vacancy is None:
                    repo.set_llm_job_status(
                        session,
                        job_id,
                        "error",
                        error_message="Resume or vacancy not found",
                    )
                    record_llm_job(
                        outcome="error",
                        kind=kind,
                        duration_seconds=time.perf_counter() - started,
                    )
                    return
                resume_text = resume.text
                vacancy_text = vacancy.cleaned_text or vacancy.raw_text or ""
                resume_id = job.resume_id
                vacancy_id = job.vacancy_id
                params = dict(job.params or {})
                repo.set_llm_job_status(session, job_id, "running")

            with _TRACER.start_as_current_span(
                "llm_job.run",
                attributes={"llm_job.id": str(job_id), "llm_job.kind": kind},
            ):
                if kind == KIND_MATCH:
                    match_service.match_single(
                        resume_id=resume_id,
                        resume_text=resume_text,
                        vacancy_id=vacancy_id,
                        vacancy_text=vacancy_text,
                    )
                elif kind == KIND_COVER_LETTER:
                    cover_letter_service.generate_single(
                        resume_id=resume_id,
                        resume_text=resume_text,
                        vacancy_id=vacancy_id,
                        vacancy_text=vacancy_text,
                        tone=str(params.get("tone") or "neutral"),
                        extra_notes=params.get("extra_notes"),
                    )
                else:
                    raise RuntimeError(f"Unknown llm job kind: {kind}")

            with session_scope() as session:
                repo.set_llm_job_status(session, job_id, "done")
            record_llm_job(
                outcome="done",
                kind=kind,
                duration_seconds=time.perf_counter() - started,
            )
        except Exception as e:
            _LOG.warning("LLM job %s failed: %s", job_id, e)
            try:
                with session_scope() as session:
                    repo.set_llm_job_status(session, job_id, "error", error_message=str(e))
            except Exception:
                _LOG.exception("Не удалось записать ошибку LLM job %s", job_id)
            record_llm_job(
                outcome="error",
                kind=kind,
                duration_seconds=time.perf_counter() - started,
            )


llm_job_runner = LlmJobRunner()
