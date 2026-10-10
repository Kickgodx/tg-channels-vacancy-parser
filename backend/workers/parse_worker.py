"""Entrypoint RQ-воркера: ``tg-jobs-parse-worker``."""

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
    host = (socket.gethostname() or "").strip() or "parse"
    return f"{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    setup_logging()
    try_load_dotenv()
    log = logging.getLogger(__name__)
    settings = get_settings()
    os.environ.setdefault("OTEL_SERVICE_NAME", "tg-jobs-parser-parse-worker")

    try:
        from fastapi import FastAPI

        setup_tracing(FastAPI(), settings)
    except Exception as e:
        log.warning("OTel для parse-worker не включён: %s", e)

    # Без fork (SimpleWorker): метрики в том же процессе, что :9102; меньше пик RAM.
    os.environ["TG_JOBS_METRICS_ROLE"] = "worker"

    metrics_port = settings.parse_worker_metrics_port
    try:
        from prometheus_client import start_http_server

        start_http_server(metrics_port)
        log.info("parse-worker metrics on :%s/metrics", metrics_port)
    except Exception as e:
        log.warning("Не удалось стартовать metrics HTTP: %s", e)

    import backend.observability  # noqa: F401
    from backend.queue.rq_conn import PARSE_QUEUE_NAME, get_redis_connection
    from backend.workers.recover_parse import recover_stuck_parse_runs

    if settings.parse_job_backend_normalized != "rq":
        log.warning(
            "PARSE_JOB_BACKEND=%s, но parse-worker рассчитан на rq — продолжаем с Redis",
            settings.parse_job_backend,
        )
    if not (settings.redis_url or "").strip():
        log.error("REDIS_URL обязателен для tg-jobs-parse-worker")
        sys.exit(1)

    try:
        n = recover_stuck_parse_runs()
        log.info("Recover on start: %s run(s)", n)
    except Exception as e:
        log.warning("Recover on start failed: %s", e)

    from rq import SimpleWorker

    conn = get_redis_connection()
    log.info("parse-worker listening queue=%s (SimpleWorker)", PARSE_QUEUE_NAME)
    name = _worker_name()
    log.info("RQ worker name=%s", name)
    worker = SimpleWorker([PARSE_QUEUE_NAME], connection=conn, name=name)
    try:
        worker.work(with_scheduler=False)
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
