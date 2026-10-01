"""FastAPI backend: парсинг, AI, PostgreSQL."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace

from backend.api.routes import (
    ai_cache,
    analytics,
    auth,
    channels,
    config,
    filters,
    fx,
    health,
    resumes,
    runs,
    vacancies,
)
from backend.db import fx_repository as fx_repo
from backend.db import repository as repo
import backend.observability  # noqa: F401 — регистрация доменных метрик и db_* gauges в /metrics
from backend.db.session import init_db, session_scope
from backend.http_logging import HttpLoggingMiddleware
from backend.tracing import setup_tracing, shutdown_tracing
from backend.settings import get_settings
from prometheus_fastapi_instrumentator import Instrumentator
from tg_jobs_parser.config.loader import load_config
import tg_jobs_parser.observability.llm_metrics  # noqa: F401 — регистрация llm_* в /metrics
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
    """Применить TTL вакансий (архивацию) в отдельной транзакции."""
    try:
        with session_scope() as session:
            repo.archive_expired_vacancies(session)
    except Exception as e:
        logging.getLogger(__name__).warning("TTL вакансий не применён: %s", e)


def _vacancy_retention_worker(stop_event: threading.Event) -> None:
    """Переходит к следующей проверке TTL каждый час, пока жив backend."""
    while not stop_event.wait(timeout=60 * 60):
        _cleanup_expired_vacancies()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger(__name__)
    log.info("Инициализация БД…")
    init_db()
    _cleanup_expired_vacancies()
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
    try:
        yield
    finally:
        retention_stop.set()
        retention_thread.join(timeout=1)
        shutdown_tracing()


app = FastAPI(
    title="tg-jobs-parser API",
    version="0.2.0",
    description="Backend: Telethon parsing, AI enrichment, PostgreSQL storage",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(
    HttpLoggingMiddleware,
    log_bodies=settings.http_log_bodies,
    body_limit=settings.http_log_body_limit,
    exclude_paths=settings.http_log_exclude_path_set,
)
setup_tracing(app, settings)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(health.router, prefix="/api/v1")
app.include_router(config.router, prefix="/api/v1")
app.include_router(channels.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(vacancies.router, prefix="/api/v1")
app.include_router(ai_cache.router, prefix="/api/v1")
app.include_router(fx.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(filters.router, prefix="/api/v1")
app.include_router(resumes.resumes_router, prefix="/api/v1")

Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    excluded_handlers=[
        "/metrics",
        "/api/v1/health",
        "/docs",
        "/openapi.json",
        "/redoc",
    ],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=s.api_host,
        port=s.api_port,
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
