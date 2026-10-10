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
    from backend.workers.recover import recover_stuck_llm_jobs

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

    from rq import Worker

    conn = get_redis_connection()
    log.info("llm-worker listening queue=%s", LLM_QUEUE_NAME)
    name = _worker_name()
    log.info("RQ worker name=%s", name)
    worker = Worker([LLM_QUEUE_NAME], connection=conn, name=name)
    try:
        worker.work(with_scheduler=False)
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
