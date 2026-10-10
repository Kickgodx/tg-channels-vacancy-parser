"""Поднять зависшие / потерянные из Redis ``llm_jobs`` обратно в очередь."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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
    """Сбросить stuck ``running`` и старые ``queued`` → снова в RQ/inprocess.

    Возвращает число задач, отправленных в очередь снова.
    """
    settings = get_settings()
    backend = settings.llm_job_backend_normalized
    stuck_after = datetime.now(UTC) - timedelta(minutes=max(1, settings.llm_job_stuck_minutes))
    requeued = 0

    with session_scope() as session:
        stuck_running = repo.list_llm_jobs_by_status(session, status="running", older_than=stuck_after)
        for row in stuck_running:
            repo.reset_llm_job_to_queued(session, row.id)
            record_llm_jobs_recover(from_status="running")
            _LOG.warning("LLM job %s: recover running→queued (stuck)", row.id)

        stale_queued = repo.list_llm_jobs_by_status(session, status="queued", older_than=stuck_after)
        candidates = [(row.id, row.rq_job_id) for row in stale_queued]

    if not candidates:
        return 0

    from backend.workers.llm_tasks import run_llm_job

    redis_conn = None
    if backend == "rq":
        from backend.queue.rq_conn import get_redis_connection

        redis_conn = get_redis_connection()

    for jid, rq_job_id in candidates:
        try:
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
