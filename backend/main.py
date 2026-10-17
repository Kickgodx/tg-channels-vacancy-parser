"""FastAPI backend: парсинг, AI, PostgreSQL."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from prometheus_fastapi_instrumentator import Instrumentator

import tg_jobs_parser.observability.llm_metrics  # noqa: F401 — регистрация llm_* в /metrics
from backend.api.routes import (
    ai_cache,
    analytics,
    auth,
    config,
    filters,
    fx,
    health,
    hh,
    llm_jobs,
    resumes,
    runs,
    vacancies,
)
from backend.db import fx_repository as fx_repo
from backend.db import hh_repository as hh_repo
from backend.db import repository as repo
from backend.db.session import init_db, session_scope
from backend.errors import register_exception_handlers
from backend.http_logging import HttpLoggingMiddleware
from backend.observability import register_db_stats_collector
from backend.settings import check_exposure, get_settings
from backend.tracing import setup_tracing, shutdown_tracing
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.logger import setup_logging

setup_logging()
try_load_dotenv()
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
_TRACER = trace.get_tracer(__name__)


def _sync_fx_on_startup() -> None:
    """Фоновая подгрузка курсов ЦБ — не блокирует приём HTTP-запросов."""
    log = logging.getLogger(__name__)
    try:
        cfg = load_config(get_settings().config_path)
        if not cfg.fx.enabled or cfg.fx.sync_days_on_startup <= 0:
            return
        with _TRACER.start_as_current_span("fx.sync_on_startup") as span:
            with session_scope() as session:
                n = fx_repo.sync_recent_days(session, cfg.fx)
            span.set_attribute("fx.rates_synced", n)
        log.info(
            "Курсы ЦБ: синхронизировано записей за последние %s дн.: %s",
            cfg.fx.sync_days_on_startup,
            n,
        )
    except Exception as e:
        log.warning("Курсы ЦБ при старте не загружены: %s", e)


def _cleanup_expired_vacancies() -> None:
    """Применить TTL вакансий (архивацию), кэш HH listings и бэкфилл грейдов HH."""
    try:
        with session_scope() as session:
            repo.archive_expired_vacancies(session)
            hh_repo.purge_expired_listings(session)
            hh_repo.backfill_hh_grades_from_experience(session)
    except Exception as e:
        logging.getLogger(__name__).warning("TTL вакансий/HH listings не применён: %s", e)


def _periodic_cleanup() -> None:
    """TTL вакансий и очистка служебных таблиц (кэш LLM, LLM-джобы, idempotency, прогоны, курсы)."""
    _cleanup_expired_vacancies()
    try:
        from backend.services.db_cleanup import purge_service_tables

        cfg = load_config(get_settings().config_path)
        purge_service_tables(cfg.retention, fx_sync_days=cfg.fx.sync_days_on_startup)
    except Exception as e:
        logging.getLogger(__name__).warning("Очистка служебных таблиц не выполнена: %s", e)


def _vacancy_retention_worker(stop_event: threading.Event) -> None:
    """Переходит к следующей проверке TTL каждый час, пока жив backend."""
    while not stop_event.wait(timeout=60 * 60):
        _periodic_cleanup()


def _start_inprocess_job_recovery() -> list[threading.Event]:
    """Поднять зависшие джобы и запустить периодический recover для backend'ов ``inprocess``.

    В режиме ``rq`` это делают воркеры. В ``inprocess`` воркеров нет: без recover
    в API прогон, оборванный рестартом, навсегда остаётся ``running`` (вечный
    409 CONFLICT_ACTIVE_RUN), а LLM-джобы съедают лимит пользователя.
    """
    from backend.workers.recover import recover_stuck_llm_jobs
    from backend.workers.recover_hh_sync import recover_stuck_hh_sync_jobs
    from backend.workers.recover_loop import start_periodic_recover
    from backend.workers.recover_parse import recover_stuck_parse_runs

    log = logging.getLogger(__name__)
    settings = get_settings()
    stops: list[threading.Event] = []
    for job_backend, recover_fn, name in (
        (settings.parse_job_backend_normalized, recover_stuck_parse_runs, "parse-recover-loop"),
        (settings.llm_job_backend_normalized, recover_stuck_llm_jobs, "llm-recover-loop"),
        (settings.hh_sync_job_backend_normalized, recover_stuck_hh_sync_jobs, "hh-recover-loop"),
    ):
        if job_backend != "inprocess":
            continue
        try:
            # Потоки прошлого процесса не пережили рестарт — queued поднимаем сразу.
            # running ждёт истечения lease: его подберёт периодический цикл.
            n = recover_fn(requeue_all_queued=True)
            if n:
                log.info("%s: recover on start, requeued=%s", name, n)
        except Exception as e:
            log.warning("%s: recover on start failed: %s", name, e)
        stops.append(
            start_periodic_recover(
                recover_fn,
                interval_seconds=settings.job_recover_interval_seconds,
                name=name,
            )
        )
    return stops


def _start_metrics_server():
    """``/metrics`` на ``API_METRICS_PORT`` — отдельно от порта API, как у воркеров.

    Порт API публикуется клиентам, порт метрик — нет: в compose Prometheus
    ходит на него по Docker-сети. Отдаёт тот же ``REGISTRY``, куда пишут
    Instrumentator (HTTP), доменные и ``db_*``-метрики.
    """
    s = get_settings()
    if not s.api_metrics_port:
        return None
    from prometheus_client import start_http_server

    log = logging.getLogger(__name__)
    try:
        server, _thread = start_http_server(s.api_metrics_port, addr=s.api_host)
    except OSError as e:
        log.warning("Не удалось стартовать metrics HTTP на %s:%s: %s", s.api_host, s.api_metrics_port, e)
        return None
    log.info("backend metrics on %s:%s/metrics", s.api_host, s.api_metrics_port)
    return server


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger(__name__)
    warning = check_exposure(get_settings())
    if warning:
        log.warning(warning)
    from backend.core_ports import register_core_ports

    # LLM в inprocess-режиме идёт в этом процессе; RPM общий с воркерами.
    register_core_ports()
    from backend.observability import init_llm_model_series

    init_llm_model_series()
    env = get_settings()
    log.info(
        "APP_ENV=%s: регистрация новых пользователей %s, тела HTTP в логах %s",
        env.app_env,
        "открыта" if env.auth_registration_open else "закрыта",
        "пишутся" if env.http_log_bodies else "не пишутся",
    )
    log.info("Инициализация БД…")
    init_db()
    _periodic_cleanup()
    log.info("Backend готов.")
    threading.Thread(
        target=_sync_fx_on_startup,
        name="fx-startup-sync",
        daemon=True,
    ).start()
    retention_stop = threading.Event()
    retention_thread = threading.Thread(
        target=_vacancy_retention_worker,
        args=(retention_stop,),
        name="vacancy-retention",
        daemon=True,
    )
    retention_thread.start()
    recover_stops = _start_inprocess_job_recovery()
    metrics_server = _start_metrics_server()
    try:
        yield
    finally:
        if metrics_server is not None:
            metrics_server.shutdown()
            metrics_server.server_close()
        for stop in recover_stops:
            stop.set()
        retention_stop.set()
        retention_thread.join(timeout=1)
        from backend.services.interactive_llm import reset_interactive_client
        from backend.services.llm_job_service import llm_job_runner

        llm_job_runner.reset()
        reset_interactive_client()
        shutdown_tracing()


app = FastAPI(
    title="tg-jobs-parser API",
    version="0.2.0",
    description="Backend: Telethon parsing, AI enrichment, PostgreSQL storage",
    lifespan=lifespan,
    # FastAPI >= 0.142 трассирует сам и добавляет свой OTLP-экспортёр к глобальному
    # provider'у — с нашей инструментацией (backend/tracing.py) это дубли span'ов
    # и трассы без исключений путей. Старые версии кладут параметр в app.extra.
    telemetry={
        "tracing": False,
        "metrics": False,
        "logs": False,
        "auto_configure": False,
    },
)

settings = get_settings()
register_exception_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Export-Truncated"],
)
app.add_middleware(
    HttpLoggingMiddleware,
    log_bodies=settings.http_log_bodies,
    body_limit=settings.http_log_body_limit,
    exclude_paths=settings.http_log_exclude_path_set,
)
setup_tracing(app, settings)
# db_* gauges (COUNT по таблицам) — только в процессе API, см. register_db_stats_collector.
register_db_stats_collector()

app.include_router(auth.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(config.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(runs.sse_router, prefix="/api/v1")
app.include_router(vacancies.router, prefix="/api/v1")
app.include_router(hh.router, prefix="/api/v1")
app.include_router(hh.sse_router, prefix="/api/v1")
app.include_router(ai_cache.router, prefix="/api/v1")
app.include_router(fx.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(filters.router, prefix="/api/v1")
app.include_router(resumes.resumes_router, prefix="/api/v1")
app.include_router(llm_jobs.router, prefix="/api/v1")

# Только сбор HTTP-метрик в REGISTRY; отдаёт их сервер на API_METRICS_PORT (_start_metrics_server).
Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=[
        "/api/v1/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    ],
).instrument(app)


def main() -> None:
    import uvicorn

    s = get_settings()
    # Объект, а не строка "backend.main:app": при `python -m backend.main` строка
    # заставила бы uvicorn импортировать модуль второй раз — второй app без
    # OTel-инструментации (её получил первый, из `__main__`).
    uvicorn.run(
        app,
        host=s.api_host,
        port=s.api_port,
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
