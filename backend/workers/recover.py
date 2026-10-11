"""Поднять зависшие / потерянные из Redis ``llm_jobs`` обратно в очередь."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.db import llm_job_repository as lj
from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_llm_jobs_recover
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)


def _rq_job_still_live(rq_job_id: str, connection) -> bool:
    """True, если RQ-задача ещё есть и не в finished/failed."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        job = Job.fetch(rq_job_id, connection=connection)
    except NoSuchJobError:
        return False
    return not (job.is_finished or job.is_failed)


def recover_stuck_llm_jobs() -> int:
    """Сбросить stuck ``running`` (expired lease) и старые ``queued`` → снова в RQ/inprocess."""
    settings = get_settings()
    backend = settings.llm_job_backend_normalized
    now = datetime.now(UTC)
    stuck_queued_after = now - timedelta(minutes=max(1, settings.llm_job_stuck_minutes))
    legacy_cutoff = stuck_queued_after
    requeued = 0
    to_requeue: list[UUID] = []
    seen: set[UUID] = set()

    with session_scope() as session:
        expired = lj.list_llm_jobs_with_expired_lease(session, now=now)
        for row in expired:
            if row.id in seen:
                continue
            if not lj.reclaim_expired_llm_job(session, row.id, now=now):
                continue
            seen.add(row.id)
            record_llm_jobs_recover(from_status="running")
            _LOG.warning("LLM job %s: recover running→queued (lease expired)", row.id)
            to_requeue.append(row.id)

        from sqlalchemy import update

        from backend.db.models import LlmJobRow

        legacy_running = [
            r
            for r in repo.list_llm_jobs_by_status(session, status="running", older_than=legacy_cutoff)
            if r.lease_until is None
        ]
        for row in legacy_running:
            if row.id in seen:
                continue
            result = session.execute(
                update(LlmJobRow)
                .where(
                    LlmJobRow.id == row.id,
                    LlmJobRow.status == "running",
                    LlmJobRow.lease_until.is_(None),
                )
                .values(
                    status="queued",
                    started_at=None,
                    finished_at=None,
                    error_message=None,
                    rq_job_id=None,
                    lease_owner=None,
                    lease_until=None,
                )
            )
            if (result.rowcount or 0) != 1:
                continue
            session.flush()
            seen.add(row.id)
            record_llm_jobs_recover(from_status="running")
            _LOG.warning("LLM job %s: recover legacy running→queued", row.id)
            to_requeue.append(row.id)

        stale_queued = repo.list_llm_jobs_by_status(session, status="queued", older_than=stuck_queued_after)
        for row in stale_queued:
            if row.id in seen:
                continue
            seen.add(row.id)
            to_requeue.append(row.id)

    if not to_requeue:
        return 0

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

            if backend == "rq":
                assert redis_conn is not None
                if rq_job_id and _rq_job_still_live(rq_job_id, redis_conn):
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
                    job_timeout=settings.llm_rq_job_timeout_seconds,
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
