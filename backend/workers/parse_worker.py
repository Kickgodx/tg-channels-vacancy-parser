"""Entrypoint RQ-воркера: ``tg-jobs-parse-worker``."""

from __future__ import annotations

from backend.settings import get_settings
from backend.workers.runner import run_rq_worker


def _setup() -> None:
    from backend.db.ai_cache import register_db_ai_cache
    from backend.services.llm_rate_gate import register_redis_rate_gate

    # Парсинг с ai.cache_backend=postgres идёт в этом процессе, не в API.
    register_db_ai_cache()
    register_redis_rate_gate()


def main() -> None:
    from backend.queue.rq_conn import PARSE_QUEUE_NAME
    from backend.workers.recover_parse import recover_stuck_parse_runs

    settings = get_settings()
    run_rq_worker(
        role="parse-worker",
        queue_name=PARSE_QUEUE_NAME,
        metrics_port=settings.parse_worker_metrics_port,
        recover_fn=recover_stuck_parse_runs,
        job_backend=settings.parse_job_backend_normalized,
        job_backend_env="PARSE_JOB_BACKEND",
        setup=_setup,
    )


if __name__ == "__main__":
    main()
