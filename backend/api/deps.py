from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.models import UserRow
from backend.db.session import get_db
from backend.services import auth_service
from backend.settings import get_settings

__all__ = [
    "get_current_user",
    "get_db",
    "get_optional_user",
    "require_admin",
    "verify_api_key",
]

_LOG = logging.getLogger(__name__)


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    settings = get_settings()
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        _LOG.warning("Запрос отклонён: неверный или отсутствующий X-API-Key")
        raise HTTPException(status_code=401, detail="Invalid API key")


def _extract_token(
    authorization: str | None,
    access_token: str | None,
) -> str | None:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return None
    return access_token


def _user_from_token(db: Session, token: str | None) -> UserRow | None:
    if not token:
        return None
    user_id = auth_service.decode_access_token(token)
    if user_id is None:
        return None
    user = db.get(UserRow, user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(
        default=None,
        description="JWT для SSE-эндпоинтов (EventSource не шлёт заголовки)",
        include_in_schema=False,
    ),
    db: Session = Depends(get_db),
) -> UserRow:
    user = _user_from_token(db, _extract_token(authorization, access_token))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None, include_in_schema=False),
    db: Session = Depends(get_db),
) -> UserRow | None:
    """Тот же разбор JWT, но без 401 — для общих эндпоинтов с per-user ветками."""
    return _user_from_token(db, _extract_token(authorization, access_token))


def require_admin(user: UserRow = Depends(get_current_user)) -> UserRow:
    """JWT + ``users.is_admin``; иначе 403."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
