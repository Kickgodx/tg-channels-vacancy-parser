"""FastAPI backend: парсинг, AI, PostgreSQL."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import channels, config, health, runs, vacancies
from backend.db.session import init_db
from backend.settings import get_settings
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.utils.logger import setup_logging

setup_logging()
try_load_dotenv()

app = FastAPI(
    title="tg-jobs-parser API",
    version="0.2.0",
    description="Backend: Telethon parsing, AI enrichment, PostgreSQL storage",
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


@app.on_event("startup")
def on_startup() -> None:
    logging.getLogger(__name__).info("Инициализация БД…")
    init_db()


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=s.api_host,
        port=s.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
