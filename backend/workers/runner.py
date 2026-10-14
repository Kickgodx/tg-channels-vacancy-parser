"""Общий запуск RQ-воркера: tracing, /metrics, recover-цикл, heartbeat и watchdog."""

from __future__ import annotations

import logging
import os
import socket
import sys
import uuid
from collections.abc import Callable

from backend.settings import get_settings
from backend.tracing import setup_tracing, shutdown_tracing
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.logger import setup_logging


def worker_name(role: str) -> str:
    """Уникальное имя RQ-воркера: в Docker у всех реплик PID=1, поэтому pid недостаточно."""
    host = (socket.gethostname() or "").strip() or role
    return f"{host}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def run_rq_worker(
    *,
    role: str,
    queue_name: str,
    metrics_port: int,
    recover_fn: Callable[[], int],
    job_backend: str,
    job_backend_env: str,
    setup: Callable[[], None] | None = None,
) -> None:
    """Блокирующий цикл SimpleWorker на ``queue_name`` до остановки."""
    setup_logging()
    try_load_dotenv()
    log = logging.getLogger(f"backend.workers.{role}")
    settings = get_settings()
    os.environ.setdefault("OTEL_SERVICE_NAME", f"tg-jobs-parser-{role}")

    # Минимальный FastAPI app не нужен — поднимаем OTEL через Settings как у backend.
    try:
        from fastapi import FastAPI

        setup_tracing(FastAPI(), settings)
    except Exception as e:
        log.warning("OTel для %s не включён: %s", role, e)

    # Без fork (SimpleWorker): метрики в том же процессе, что и порт метрик; меньше пик RAM
    # (Worker fork'ает work horse на каждую джобу — COW-копия + счётчики теряются).
    # DB COUNT-collector только на backend; db_pool_* — и здесь (свой QueuePool).
    os.environ["TG_JOBS_METRICS_ROLE"] = "worker"

    try:
        from prometheus_client import start_http_server

        start_http_server(metrics_port)
        log.info("%s metrics on :%s/metrics", role, metrics_port)
    except Exception as e:
        log.warning("Не удалось стартовать metrics HTTP: %s", e)

    # Регистрация доменных метрик (counters/histograms).
    import backend.observability  # noqa: F401
    from backend.queue.rq_conn import get_redis_connection
    from backend.workers.liveness import RQ_WORKER_TTL, LivenessWorker, start_watchdog

    if setup is not None:
        setup()

    if job_backend != "rq":
        log.warning("%s=%s, но %s рассчитан на rq — продолжаем с Redis", job_backend_env, job_backend, role)
    if not (settings.redis_url or "").strip():
        log.error("REDIS_URL обязателен для %s", role)
        sys.exit(1)

    try:
        n = recover_fn()
        log.info("Recover on start: %s job(s)", n)
    except Exception as e:
        log.warning("Recover on start failed: %s", e)

    from backend.workers.recover_loop import start_periodic_recover

    recover_stop = start_periodic_recover(
        recover_fn,
        interval_seconds=settings.job_recover_interval_seconds,
        name=f"{role}-recover-loop",
    )
    watchdog_stop = start_watchdog(settings.worker_stall_exit_seconds, name=role)

    conn = get_redis_connection()
    name = worker_name(role)
    log.info("%s listening queue=%s (SimpleWorker, name=%s)", role, queue_name, name)
    worker = LivenessWorker([queue_name], connection=conn, name=name, worker_ttl=RQ_WORKER_TTL)
    try:
        worker.work(with_scheduler=False)
    finally:
        recover_stop.set()
        if watchdog_stop is not None:
            watchdog_stop.set()
        shutdown_tracing()
