"""Entrypoint RQ-воркера: ``tg-jobs-parse-worker``."""

from __future__ import annotations

from backend.settings import get_settings
from backend.workers.runner import run_rq_worker


def _setup() -> None:
    from backend.core_ports import register_core_ports
    from backend.observability import init_llm_model_series

    # Парсинг (и его LLM-разбор) идёт в этом процессе, не в API.
    register_core_ports()
    init_llm_model_series()


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
