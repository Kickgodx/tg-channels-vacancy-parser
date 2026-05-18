from __future__ import annotations

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.settings import get_settings

__all__ = ["get_db", "verify_api_key"]


def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
