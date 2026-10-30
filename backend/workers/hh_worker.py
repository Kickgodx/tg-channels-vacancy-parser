"""Entrypoint RQ-воркера: ``jobdesk-hh-worker`` (фоновый HH sync)."""

from __future__ import annotations

from backend.settings import get_settings
from backend.workers.runner import run_rq_worker


def main() -> None:
    from backend.queue.rq_conn import HH_QUEUE_NAME
    from backend.workers.recover_hh_sync import recover_stuck_hh_sync_jobs

    settings = get_settings()
    run_rq_worker(
        role="hh-worker",
        queue_name=HH_QUEUE_NAME,
        metrics_port=settings.hh_worker_metrics_port,
        recover_fn=recover_stuck_hh_sync_jobs,
        job_backend=settings.hh_sync_job_backend_normalized,
        job_backend_env="HH_SYNC_JOB_BACKEND",
    )


if __name__ == "__main__":
    main()
