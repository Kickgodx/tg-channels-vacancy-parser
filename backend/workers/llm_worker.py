"""Entrypoint RQ-воркера: ``tg-jobs-llm-worker``."""

from __future__ import annotations

import logging
import os
import socket
import sys
import uuid

from backend.settings import get_settings
from backend.tracing import setup_tracing, shutdown_tracing
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.logger import setup_logging


def _worker_name() -> str:
    """Уникальное имя RQ-воркера: в Docker у всех реплик PID=1, поэтому pid недостаточно."""
    host = (socket.gethostname() or "").strip() or "llm"
    return f"{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    setup_logging()
    try_load_dotenv()
    log = logging.getLogger(__name__)
    settings = get_settings()
    os.environ.setdefault("OTEL_SERVICE_NAME", "tg-jobs-parser-llm-worker")

    # Минимальный FastAPI app не нужен — поднимаем OTEL через Settings как у backend.
    try:
        from fastapi import FastAPI

        setup_tracing(FastAPI(), settings)
    except Exception as e:
        log.warning("OTel для llm-worker не включён: %s", e)

    # Без fork (SimpleWorker): метрики в том же процессе, что :9101; меньше пик RAM
    # (Worker fork’ает work horse на каждую джобу — COW-копия + счётчики теряются).
    # DB COUNT-collector только на backend; db_pool_* — и здесь (свой QueuePool).
    os.environ["TG_JOBS_METRICS_ROLE"] = "worker"

    metrics_port = settings.llm_worker_metrics_port
    try:
        from prometheus_client import start_http_server

        start_http_server(metrics_port)
        log.info("llm-worker metrics on :%s/metrics", metrics_port)
    except Exception as e:
        log.warning("Не удалось стартовать metrics HTTP: %s", e)

    # Регистрация доменных метрик (counters/histograms).
    import backend.observability  # noqa: F401
    from backend.queue.rq_conn import LLM_QUEUE_NAME, get_redis_connection
    from backend.services.llm_rate_gate import register_redis_rate_gate
    from backend.workers.recover import recover_stuck_llm_jobs

    # Реплики llm-worker делят один ai.requests_per_minute, а не умножают его.
    register_redis_rate_gate()

    if settings.llm_job_backend_normalized != "rq":
        log.warning(
            "LLM_JOB_BACKEND=%s, но llm-worker рассчитан на rq — продолжаем с Redis",
            settings.llm_job_backend,
        )
    if not (settings.redis_url or "").strip():
        log.error("REDIS_URL обязателен для tg-jobs-llm-worker")
        sys.exit(1)

    try:
        n = recover_stuck_llm_jobs()
        log.info("Recover on start: %s job(s)", n)
    except Exception as e:
        log.warning("Recover on start failed: %s", e)

    from backend.workers.recover_loop import start_periodic_recover

    recover_stop = start_periodic_recover(
        recover_stuck_llm_jobs,
        interval_seconds=settings.job_recover_interval_seconds,
        name="llm-recover-loop",
    )

    from rq import SimpleWorker

    conn = get_redis_connection()
    log.info("llm-worker listening queue=%s (SimpleWorker)", LLM_QUEUE_NAME)
    name = _worker_name()
    log.info("RQ worker name=%s", name)
    worker = SimpleWorker([LLM_QUEUE_NAME], connection=conn, name=name)
    try:
        worker.work(with_scheduler=False)
    finally:
        recover_stop.set()
        shutdown_tracing()


if __name__ == "__main__":
    main()
