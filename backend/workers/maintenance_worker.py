"""Entrypoint ``jobdesk-maintenance-worker``: фоновое обслуживание каталога вне процессов API.

Индексация эмбеддингов и оценки зарплат, недельный обзор рынка (LLM), карта рынка (t-SNE)
и пересчёт карты по запросу администратора (``backend/maintenance.py``). Задачи идут по
очереди в главном потоке, раз в ``TICK_SECONDS`` — новый цикл.

Реплик может быть несколько: работает одна — держащая advisory lock (``MAINTENANCE_LOCK_KEY``),
остальные ждут и берут роль, если она упала. API при ``MAINTENANCE_BACKEND=worker`` эти задачи
не выполняет; без воркера (``inprocess``) их делает лидер API.

Heartbeat (``worker_heartbeat_timestamp_seconds``) бьёт между циклами: задача может идти
минутами (t-SNE на тысячах точек, первая индексация), поэтому порог watchdog —
``STALL_EXIT_SECONDS``, а не общий ``WORKER_STALL_EXIT_SECONDS`` RQ-воркеров.
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from backend.settings import get_settings

# Ключ pg_advisory_lock исполнителя обслуживания (≠ лидеру API, миграциям и HH).
MAINTENANCE_LOCK_KEY = 7_242_031_003
TICK_SECONDS = 10.0
# Цикл дольше часа — задача зависла: процесс завершается, restart policy поднимает его.
STALL_EXIT_SECONDS = 3600


def run_loop(stop: threading.Event, *, lock, duties, beat, tick_seconds: float = TICK_SECONDS) -> None:
    """Цикл исполнителя: держит lock — выполняет ``duties.tick()``, иначе ждёт роль."""
    log = logging.getLogger("backend.workers.maintenance-worker")
    leading = False
    while not stop.is_set():
        beat()
        try:
            held = lock.try_acquire() and lock.alive()
        except Exception as e:
            log.warning("Lock обслуживания не получен: %s", e)
            held = False
        if held != leading:
            leading = held
            log.info("Обслуживание каталога: %s", "выполняет этот процесс" if held else "роль у другого процесса")
        if held:
            try:
                duties.tick()
            except Exception:
                log.exception("Цикл обслуживания упал")
        beat()
        stop.wait(tick_seconds)
    lock.release()


def main() -> None:
    from backend.db.session import set_pool_role
    from jobdesk.utils.env_loader import try_load_dotenv
    from jobdesk.utils.logger import setup_logging

    role = "maintenance-worker"
    set_pool_role(role)
    setup_logging()
    try_load_dotenv()
    log = logging.getLogger(f"backend.workers.{role}")
    settings = get_settings()
    os.environ.setdefault("OTEL_SERVICE_NAME", f"jobdesk-{role}")

    from backend.tracing import setup_tracing, shutdown_tracing

    try:
        from fastapi import FastAPI

        setup_tracing(FastAPI(), settings)
    except Exception as e:
        log.warning("OTel для %s не включён: %s", role, e)

    try:
        from prometheus_client import start_http_server

        start_http_server(settings.maintenance_worker_metrics_port)
        log.info("%s metrics on :%s/metrics", role, settings.maintenance_worker_metrics_port)
    except Exception as e:
        log.warning("Не удалось стартовать metrics HTTP: %s", e)

    import backend.observability  # noqa: F401 — регистрация доменных метрик
    from backend.core_ports import register_core_ports
    from backend.leader import LeaderLock
    from backend.maintenance import MaintenanceDuties
    from backend.workers.liveness import beat, start_watchdog

    # Обзор рынка идёт через LLM: без кэша и общего RPM AIEnrichmentService не создаётся.
    register_core_ports()
    if settings.maintenance_backend != "worker":
        log.warning(
            "MAINTENANCE_BACKEND=%s: обслуживание выполняет и лидер API — задайте worker ему и воркеру",
            settings.maintenance_backend,
        )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    watchdog_stop = start_watchdog(STALL_EXIT_SECONDS, name=role)
    log.info("%s запущен: цикл раз в %s с", role, TICK_SECONDS)
    try:
        run_loop(stop, lock=LeaderLock(MAINTENANCE_LOCK_KEY), duties=MaintenanceDuties(threaded=False), beat=beat)
    finally:
        if watchdog_stop is not None:
            watchdog_stop.set()
        shutdown_tracing()


if __name__ == "__main__":
    main()
