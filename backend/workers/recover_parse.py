"""Поднять зависшие / потерянные из Redis ``parse_runs`` обратно в очередь."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.db import parse_run_repository as pr
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
    """Сбросить stuck ``running`` (expired lease) и старые ``queued`` → снова в RQ/inprocess.

    Возвращает число прогонов, отправленных в очередь снова.
    Reclaim running — атомарный conditional UPDATE (fencing).
    """
    settings = get_settings()
    backend = settings.parse_job_backend_normalized
    now = datetime.now(UTC)
    stuck_queued_after = now - timedelta(minutes=max(1, settings.parse_job_stuck_minutes))
    legacy_cutoff = stuck_queued_after
    requeued = 0
    to_requeue: list[UUID] = []
    seen: set[UUID] = set()

    with session_scope() as session:
        try:
            from backend.db import idempotency_repository as idem_repo

            purged = idem_repo.purge_expired_idempotency(session)
            if purged:
                _LOG.info("idempotency_keys: purged expired=%s", purged)
        except Exception as e:
            _LOG.debug("idempotency purge skipped: %s", e)

        expired = pr.list_parse_runs_with_expired_lease(session, now=now)
        for row in expired:
            if row.id in seen:
                continue
            outcome = pr.reclaim_expired_parse_run(session, row.id, now=now)
            if outcome is None:
                continue
            seen.add(row.id)
            record_parse_jobs_recover(from_status="running")
            if outcome == "cancelled":
                _LOG.warning("Parse run %s: recover running→cancelled (cancel_requested)", row.id)
                continue
            _LOG.warning("Parse run %s: recover running→queued (lease expired)", row.id)
            to_requeue.append(row.id)

        legacy_running = [
            r
            for r in repo.list_parse_runs_by_status(session, status="running", older_than=legacy_cutoff)
            if r.lease_until is None
        ]
        for row in legacy_running:
            if row.id in seen:
                continue
            outcome = pr.reclaim_legacy_stale_parse_run(session, row.id, older_than=legacy_cutoff)
            if outcome is None:
                continue
            seen.add(row.id)
            record_parse_jobs_recover(from_status="running")
            if outcome == "cancelled":
                _LOG.warning("Parse run %s: recover legacy running→cancelled", row.id)
                continue
            _LOG.warning("Parse run %s: recover legacy running→queued", row.id)
            to_requeue.append(row.id)

        stale_queued = repo.list_parse_runs_by_status(session, status="queued", older_than=stuck_queued_after)
        for row in stale_queued:
            if row.id in seen or row.cancel_requested:
                continue
            seen.add(row.id)
            # Сохраняем rq_job_id для live-check до очистки.
            to_requeue.append(row.id)

    if not to_requeue:
        return 0

    from backend.workers.parse_tasks import run_parse_job

    redis_conn = None
    if backend == "rq":
        from backend.queue.rq_conn import get_redis_connection

        redis_conn = get_redis_connection()

    for rid in to_requeue:
        try:
            rq_job_id: str | None = None
            with session_scope() as session:
                row = repo.get_parse_run(session, rid)
                if row is None or row.status != "queued" or row.cancel_requested:
                    continue
                rq_job_id = row.rq_job_id

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
