"""FastAPI backend: парсинг, AI, PostgreSQL."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import ai_cache, channels, config, fx, health, runs, vacancies
from backend.db import fx_repository as fx_repo
from backend.db.session import init_db, session_scope
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.logger import setup_logging

setup_logging()
try_load_dotenv()


def _sync_fx_on_startup() -> None:
    """Фоновая подгрузка курсов ЦБ — не блокирует приём HTTP-запросов."""
    log = logging.getLogger(__name__)
    try:
        cfg = load_config(get_settings().config_path)
        if not cfg.fx.enabled or cfg.fx.sync_days_on_startup <= 0:
            return
        with session_scope() as session:
            n = fx_repo.sync_recent_days(session, cfg.fx)
        log.info(
            "Курсы ЦБ: синхронизировано записей за последние %s дн.: %s",
            cfg.fx.sync_days_on_startup,
            n,
        )
    except Exception as e:
        log.warning("Курсы ЦБ при старте не загружены: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log = logging.getLogger(__name__)
    log.info("Инициализация БД…")
    init_db()
    log.info("Backend готов.")
    threading.Thread(
        target=_sync_fx_on_startup,
        name="fx-startup-sync",
        daemon=True,
    ).start()
    yield


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
)

app.include_router(health.router, prefix="/api/v1")
app.include_router(config.router, prefix="/api/v1")
app.include_router(channels.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(vacancies.router, prefix="/api/v1")
app.include_router(ai_cache.router, prefix="/api/v1")
app.include_router(fx.router, prefix="/api/v1")


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
