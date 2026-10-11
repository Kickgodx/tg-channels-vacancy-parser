from __future__ import annotations

import logging

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from backend.db.models import UserRow
from backend.db.session import get_db
from backend.errors import CODE_FORBIDDEN, CODE_NOT_AUTHENTICATED, http_error
from backend.services import auth_service
from backend.settings import get_settings

__all__ = [
    "get_current_user",
    "get_db",
    "get_optional_user",
    "get_sse_user",
    "require_admin",
    "verify_api_key",
    "verify_api_key_sse",
]

_LOG = logging.getLogger(__name__)


def _check_api_key(provided: str | None) -> None:
    settings = get_settings()
    if not settings.api_key:
        return
    if provided != settings.api_key:
        _LOG.warning("Запрос отклонён: неверный или отсутствующий X-API-Key")
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Invalid API key")


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Только заголовок X-API-Key (обычные REST-запросы)."""
    _check_api_key(x_api_key)


def verify_api_key_sse(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(
        default=None,
        description="API key для SSE/EventSource (заголовки недоступны)",
        include_in_schema=False,
    ),
) -> None:
    """X-API-Key или ``?api_key=`` — только для SSE/EventSource."""
    _check_api_key(x_api_key or api_key)


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _extract_token(
    authorization: str | None,
    access_token: str | None,
) -> str | None:
    bearer = _extract_bearer(authorization)
    if bearer:
        return bearer
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
    db: Session = Depends(get_db),
) -> UserRow:
    """JWT только из ``Authorization: Bearer`` (не из query)."""
    user = _user_from_token(db, _extract_bearer(authorization))
    if user is None:
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
    return user


def get_sse_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(
        default=None,
        description="JWT для SSE-эндпоинтов (EventSource не шлёт заголовки)",
        include_in_schema=False,
    ),
    db: Session = Depends(get_db),
) -> UserRow:
    """JWT из Bearer или ``?access_token=`` — только для SSE."""
    user = _user_from_token(db, _extract_token(authorization, access_token))
    if user is None:
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> UserRow | None:
    """Тот же разбор JWT (только Bearer), но без 401 — для общих эндпоинтов."""
    return _user_from_token(db, _extract_bearer(authorization))


def require_admin(user: UserRow = Depends(get_current_user)) -> UserRow:
    """JWT + ``users.is_admin``; иначе 403."""
    if not user.is_admin:
        raise http_error(403, CODE_FORBIDDEN, "Admin only")
    return user
