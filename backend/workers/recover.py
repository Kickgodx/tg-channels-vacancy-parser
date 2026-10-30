"""Поднять зависшие / потерянные из Redis ``llm_jobs`` обратно в очередь."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.db import llm_job_repository as lj
from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_llm_job, record_llm_jobs_recover
from backend.services.llm_job_events import publish_llm_job_event
from backend.settings import get_settings
from backend.workers.recover_loop import rq_job_still_live
from jobdesk.config.loader import load_config

_LOG = logging.getLogger(__name__)


def recover_stuck_llm_jobs(*, requeue_all_queued: bool = False) -> int:
    """Сбросить stuck ``running`` (expired lease) и старые ``queued`` → снова в RQ/inprocess.

    Джоба, исчерпавшая ``LLM_JOB_MAX_ATTEMPTS``, уходит в ``error`` вместо requeue.
    ``requeue_all_queued`` — поднять и свежие ``queued`` (старт API в режиме inprocess:
    пул прошлого процесса уже не существует).
    """
    settings = get_settings()
    backend = settings.llm_job_backend_normalized
    now = datetime.now(UTC)
    stuck_queued_after = now - timedelta(minutes=max(1, settings.llm_job_stuck_minutes))
    legacy_cutoff = stuck_queued_after
    requeued = 0
    to_requeue: list[UUID] = []
    exhausted: list[tuple[UUID, str]] = []  # (id, kind) — ушли в error по лимиту попыток
    cancelled: list[tuple[UUID, str]] = []  # (id, kind) — отмена запрошена до потери воркера
    seen: set[UUID] = set()

    def _finish_reclaimed(job_id: UUID, kind: str, outcome: str, *, legacy: bool = False) -> None:
        seen.add(job_id)
        record_llm_jobs_recover(from_status="running")
        label = "recover legacy running" if legacy else "recover running"
        if outcome == "queued":
            _LOG.warning("LLM job %s: %s→queued%s", job_id, label, "" if legacy else " (lease expired)")
            to_requeue.append(job_id)
        elif outcome == "cancelled":
            _LOG.warning("LLM job %s: %s→cancelled (отмена запрошена)", job_id, label)
            cancelled.append((job_id, kind))
        else:
            _LOG.error("LLM job %s: %s→error (исчерпан лимит попыток)", job_id, label)
            exhausted.append((job_id, kind))

    with session_scope() as session:
        expired = lj.list_llm_jobs_with_expired_lease(session, now=now)
        for row in expired:
            if row.id in seen:
                continue
            job_id, kind = row.id, row.kind  # reclaim делает expire_all — читаем до него
            outcome = lj.reclaim_expired_llm_job(session, job_id, now=now)
            if outcome is None:
                continue
            _finish_reclaimed(job_id, kind, outcome)

        legacy_running = [
            r
            for r in repo.list_llm_jobs_by_status(session, status="running", older_than=legacy_cutoff)
            if r.lease_until is None
        ]
        for row in legacy_running:
            if row.id in seen:
                continue
            job_id, kind = row.id, row.kind
            outcome = lj.reclaim_legacy_stale_llm_job(session, job_id)
            if outcome is None:
                continue
            _finish_reclaimed(job_id, kind, outcome, legacy=True)

        stale_queued = repo.list_llm_jobs_by_status(
            session,
            status="queued",
            older_than=None if requeue_all_queued else stuck_queued_after,
        )
        for row in stale_queued:
            if row.id in seen:
                continue
            seen.add(row.id)
            to_requeue.append(row.id)

    # После commit: клиент, получив событие, перечитывает уже финальный статус.
    for job_id, kind in exhausted:
        message = lj.attempts_exhausted_message(max(1, settings.llm_job_max_attempts))
        publish_llm_job_event(job_id, status="error", kind=kind, error_message=message)
        record_llm_job(outcome="error", kind=kind)
    for job_id, kind in cancelled:
        publish_llm_job_event(job_id, status="cancelled", kind=kind)
        record_llm_job(outcome="cancelled", kind=kind)

    if not to_requeue:
        return 0

    from backend.services.llm_job_service import rq_job_timeout
    from backend.workers.llm_tasks import run_llm_job

    redis_conn = None
    if backend == "rq":
        from backend.queue.rq_conn import get_redis_connection

        redis_conn = get_redis_connection()

    for jid in to_requeue:
        try:
            rq_job_id: str | None = None
            with session_scope() as session:
                row = repo.get_llm_job(session, jid)
                if row is None or row.status != "queued":
                    continue
                rq_job_id = row.rq_job_id
                timeout = rq_job_timeout(row.kind, row.params)

            if backend == "rq":
                assert redis_conn is not None
                if rq_job_id and rq_job_still_live(rq_job_id, redis_conn):
                    _LOG.info(
                        "LLM job %s: skip recover, RQ job %s ещё в очереди/выполняется",
                        jid,
                        rq_job_id,
                    )
                    continue

                from backend.queue.rq_conn import get_llm_queue

                if rq_job_id:
                    with session_scope() as session:
                        repo.set_llm_job_rq_job_id(session, jid, None)

                rq_job = get_llm_queue().enqueue(
                    run_llm_job,
                    str(jid),
                    job_timeout=timeout,
                    result_ttl=3600,
                    failure_ttl=86400,
                )
                with session_scope() as session:
                    repo.set_llm_job_rq_job_id(session, jid, str(rq_job.id))
            else:
                from backend.services.llm_job_service import llm_job_runner

                cfg = load_config(settings.config_path)
                pool = llm_job_runner._ensure_pool(cfg.ai.interactive_concurrency)
                pool.submit(run_llm_job, str(jid))
            requeued += 1
            record_llm_jobs_recover(from_status="queued")
        except Exception as e:
            _LOG.warning("Recover enqueue %s failed: %s", jid, e)

    if requeued:
        _LOG.info("LLM jobs recover: requeued=%s backend=%s", requeued, backend)
    return requeued
