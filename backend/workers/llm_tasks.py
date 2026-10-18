"""Исполнение LLM-джобы (импортируется RQ по dotted-path)."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from backend.db import llm_job_repository as llm_jobs
from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_llm_job, record_llm_worker_job
from backend.services import cover_letter_service, import_service, match_service
from backend.services.llm_job_events import publish_llm_job_event
from backend.services.llm_job_service import KIND_COVER_LETTER, KIND_IMPORT, KIND_MATCH, LlmJobCancelled
from backend.workers import liveness

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)
_LEASE_HEARTBEAT_S = 30.0


def run_llm_job(job_id: str, traceparent: str | None = None) -> None:
    """Выполнить одну запись ``llm_jobs`` (вызывается in-process или RQ worker)."""
    started = time.perf_counter()
    kind = "unknown"
    jid = uuid.UUID(str(job_id))
    ctx = None
    if traceparent:
        try:
            from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

            ctx = TraceContextTextMapPropagator().extract(carrier={"traceparent": traceparent})
        except Exception:
            ctx = None

    with _TRACER.start_as_current_span(
        "llm_job.run",
        context=ctx,
        kind=SpanKind.CONSUMER,
        attributes={"llm_job.id": str(jid)},
    ) as span:
        stop_lease = threading.Event()
        lease_lost = threading.Event()
        lease_thread: threading.Thread | None = None
        lease_owner = ""
        try:
            from backend.db.llm_job_repository import new_llm_lease_owner

            lease_owner = new_llm_lease_owner()
            with session_scope() as session:
                job = repo.claim_llm_job(session, jid, lease_owner=lease_owner)
                if job is None:
                    return
                kind = job.kind
                span.set_attribute("llm_job.kind", kind)
                user_id = job.user_id
                params = dict(job.params or {})
                job_result = dict(job.result) if job.result else None
                resume = repo.get_resume(session, job.resume_id) if job.resume_id is not None else None
                vacancy = repo.get_vacancy_row(session, job.vacancy_id) if job.vacancy_id is not None else None
                if kind != KIND_IMPORT and (resume is None or vacancy is None):
                    if repo.set_llm_job_status(
                        session,
                        jid,
                        "error",
                        error_message="Resume or vacancy not found",
                        lease_owner=lease_owner,
                    ):
                        llm_jobs.refund_llm_job_quota(session, jid)
                    publish_llm_job_event(jid, status="error", kind=kind, error_message="Resume or vacancy not found")
                    record_llm_job(
                        outcome="error",
                        kind=kind,
                        duration_seconds=time.perf_counter() - started,
                    )
                    record_llm_worker_job(
                        kind=kind,
                        outcome="error",
                        duration_seconds=time.perf_counter() - started,
                    )
                    return
                resume_text = resume.text if resume is not None else ""
                vacancy_text = (vacancy.cleaned_text or vacancy.raw_text or "") if vacancy is not None else ""
                resume_id = job.resume_id
                vacancy_id = job.vacancy_id
            publish_llm_job_event(jid, status="running", kind=kind)
            record_llm_worker_job(kind=kind, outcome="started")

            def _lease_loop() -> None:
                while True:
                    try:
                        with session_scope() as session:
                            ok = repo.touch_llm_job_lease(session, jid, lease_owner=lease_owner)
                        if not ok:
                            _LOG.warning("LLM job %s: lease lost (fencing)", jid)
                            lease_lost.set()
                            return
                        liveness.beat()
                    except Exception as e:
                        _LOG.debug("LLM job %s lease heartbeat: %s", jid, e)
                    if stop_lease.wait(timeout=_LEASE_HEARTBEAT_S):
                        return

            lease_thread = threading.Thread(target=_lease_loop, name=f"llm-lease-{jid}", daemon=True)
            lease_thread.start()

            def is_cancelled() -> bool:
                """Флаг отмены из БД — короткая сессия в точке проверки."""
                with session_scope() as session:
                    return llm_jobs.is_llm_job_cancel_requested(session, jid)

            # Отмену могли запросить, пока задача переходила из очереди в работу.
            if is_cancelled():
                raise LlmJobCancelled("Задача отменена до начала работы")
            if kind == KIND_IMPORT:
                if not import_service.run_import_job(
                    jid,
                    user_id=user_id,
                    params=params,
                    result=job_result,
                    lease_owner=lease_owner,
                    is_cancelled=is_cancelled,
                ):
                    lease_lost.set()
            elif kind == KIND_MATCH:
                match_service.match_single(
                    resume_id=resume_id,
                    resume_text=resume_text,
                    vacancy_id=vacancy_id,
                    vacancy_text=vacancy_text,
                    is_cancelled=is_cancelled,
                )
            elif kind == KIND_COVER_LETTER:
                cover_letter_service.generate_single(
                    resume_id=resume_id,
                    resume_text=resume_text,
                    vacancy_id=vacancy_id,
                    vacancy_text=vacancy_text,
                    tone=str(params.get("tone") or "neutral"),
                    extra_notes=params.get("extra_notes"),
                    is_cancelled=is_cancelled,
                )
            else:
                raise RuntimeError(f"Unknown llm job kind: {kind}")

            if lease_lost.is_set():
                _LOG.warning("LLM job %s: результат отброшен — lease lost", jid)
                return

            with session_scope() as session:
                wrote = repo.set_llm_job_status(session, jid, "done", lease_owner=lease_owner)
            if wrote is None:
                lease_lost.set()
                _LOG.warning("LLM job %s: done no-op (fencing)", jid)
                return
            publish_llm_job_event(jid, status="done", kind=kind)
            dur = time.perf_counter() - started
            record_llm_job(outcome="done", kind=kind, duration_seconds=dur)
            record_llm_worker_job(kind=kind, outcome="done", duration_seconds=dur)
        except LlmJobCancelled as e:
            _LOG.info("LLM job %s: отменена (%s)", jid, e)
            if lease_lost.is_set():
                return
            with session_scope() as session:
                wrote = repo.set_llm_job_status(session, jid, "cancelled", lease_owner=lease_owner)
                # Отмена до ответа модели — вызов не оплачен, возвращаем его в квоту.
                if wrote is not None and not e.model_answered:
                    llm_jobs.refund_llm_job_quota(session, jid)
            if wrote is None:
                _LOG.warning("LLM job %s: cancelled no-op (fencing)", jid)
                return
            publish_llm_job_event(jid, status="cancelled", kind=kind)
            dur = time.perf_counter() - started
            record_llm_job(outcome="cancelled", kind=kind, duration_seconds=dur)
            record_llm_worker_job(kind=kind, outcome="cancelled", duration_seconds=dur)
        except Exception as e:
            _LOG.warning("LLM job %s failed: %s", jid, e)
            if not lease_lost.is_set() and lease_owner:
                try:
                    with session_scope() as session:
                        wrote = repo.set_llm_job_status(
                            session, jid, "error", error_message=str(e), lease_owner=lease_owner
                        )
                        # Ответа модели пользователь не получил (отказ, бюджет, лимит провайдера).
                        if wrote is not None:
                            llm_jobs.refund_llm_job_quota(session, jid)
                    if wrote is None:
                        lease_lost.set()
                except Exception:
                    _LOG.exception("Не удалось записать ошибку LLM job %s", jid)
            if not lease_lost.is_set():
                publish_llm_job_event(jid, status="error", kind=kind, error_message=str(e))
            dur = time.perf_counter() - started
            record_llm_job(outcome="error", kind=kind, duration_seconds=dur)
            record_llm_worker_job(kind=kind, outcome="error", duration_seconds=dur)
            raise
        finally:
            stop_lease.set()
            if lease_thread is not None:
                lease_thread.join(timeout=1.0)
