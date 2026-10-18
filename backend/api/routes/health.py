from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text

from backend.db.session import get_engine
from backend.schemas import HealthResponse
from backend.settings import get_settings
from tg_jobs_parser.config.loader import ConfigLoadError, load_config

_LOG = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    db_ok = False
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception as e:
        _LOG.warning("health: БД недоступна: %s", e)
    # Файл может существовать, но не загружаться (YAML, схема, ключи, модели).
    # Причину — только в журнал: /health анонимный, а в тексте пути и id моделей.
    config_ok = False
    try:
        load_config(settings.config_path)
        config_ok = True
    except ConfigLoadError as e:
        _LOG.warning("health: конфигурация не загружается: %s", e)
    status = "ok" if db_ok and config_ok else "degraded"
    return HealthResponse(status=status, database=db_ok, config_loaded=config_ok)
