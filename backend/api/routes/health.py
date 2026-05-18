from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import text

from backend.db.session import get_engine
from backend.schemas import HealthResponse
from backend.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    db_ok = False
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False
    config_ok = Path(settings.config_path).is_file()
    status = "ok" if db_ok and config_ok else "degraded"
    return HealthResponse(status=status, database=db_ok, config_loaded=config_ok)
