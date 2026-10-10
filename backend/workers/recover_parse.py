"""Поднять зависшие / потерянные из Redis ``parse_runs`` обратно в очередь."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import record_parse_jobs_recover
from backend.settings import get_settings

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


def recover_stuck_parse_runs() -> int:
    """Сбросить stuck ``running`` и старые ``queued`` → снова в RQ/inprocess.

    Возвращает число прогонов, отправленных в очередь снова.
    ``cancel_requested`` — re-cancel через ``reset_parse_run_to_queued``, без enqueue.
    """
    settings = get_settings()
    backend = settings.parse_job_backend_normalized
    stuck_after = datetime.now(UTC) - timedelta(minutes=max(1, settings.parse_job_stuck_minutes))
    requeued = 0

    with session_scope() as session:
        stuck_running = repo.list_parse_runs_by_status(session, status="running", older_than=stuck_after)
        for row in stuck_running:
            if row.cancel_requested:
                repo.reset_parse_run_to_queued(session, row.id)
                record_parse_jobs_recover(from_status="running")
                _LOG.warning("Parse run %s: recover running→cancelled (cancel_requested)", row.id)
                continue
            repo.reset_parse_run_to_queued(session, row.id)
            record_parse_jobs_recover(from_status="running")
            _LOG.warning("Parse run %s: recover running→queued (stuck)", row.id)

        stale_queued = repo.list_parse_runs_by_status(session, status="queued", older_than=stuck_after)
        candidates = [(row.id, row.rq_job_id) for row in stale_queued if not row.cancel_requested]

    if not candidates:
        return 0

    from backend.workers.parse_tasks import run_parse_job

    redis_conn = None
    if backend == "rq":
        from backend.queue.rq_conn import get_redis_connection

        redis_conn = get_redis_connection()

    for rid, rq_job_id in candidates:
        try:
            if backend == "rq":
                assert redis_conn is not None
                if rq_job_id and _rq_job_still_live(rq_job_id, redis_conn):
                    _LOG.info(
                        "Parse run %s: skip recover, RQ job %s ещё в очереди/выполняется",
                        rid,
                        rq_job_id,
                    )
                    continue

                from backend.queue.rq_conn import get_parse_queue

                if rq_job_id:
                    with session_scope() as session:
                        repo.set_parse_run_rq_job_id(session, rid, None)

                rq_job = get_parse_queue().enqueue(
                    run_parse_job,
                    str(rid),
                    None,
                    job_timeout=settings.parse_rq_job_timeout_seconds,
                    result_ttl=3600,
                    failure_ttl=86400,
                )
                with session_scope() as session:
                    repo.set_parse_run_rq_job_id(session, rid, str(rq_job.id))
            else:
                import threading

                threading.Thread(
                    target=run_parse_job,
                    args=(str(rid), None),
                    name=f"parse-recover-{rid}",
                    daemon=True,
                ).start()
            requeued += 1
            record_parse_jobs_recover(from_status="queued")
        except Exception as e:
            _LOG.warning("Recover enqueue %s failed: %s", rid, e)

    if requeued:
        _LOG.info("Parse runs recover: requeued=%s backend=%s", requeued, backend)
    return requeued
