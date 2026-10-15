from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, Query, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy.orm import Session

from backend.db.models import UserRow
from backend.db.session import get_db
from backend.errors import CODE_FORBIDDEN, CODE_NOT_AUTHENTICATED, http_error
from backend.services import auth_service, sse_tickets
from backend.settings import get_settings

__all__ = [
    "SseCredentials",
    "authenticate_sse",
    "bearer_token",
    "get_current_user",
    "get_db",
    "require_admin",
    "sse_credentials",
    "verify_api_key",
]

_LOG = logging.getLogger(__name__)

# Security-схемы, а не голый Header: Swagger UI не отправляет header-параметр
# ``Authorization`` (OpenAPI велит его игнорировать) — токен задаётся через «Authorize».
# Две схемы на один заголовок — два способа в диалоге: вставить готовый JWT или
# войти email/паролем (OAuth2 password flow через /auth/token).
# auto_error=False: 401 с нашим {code, message} отдают зависимости ниже.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT из POST /api/v1/auth/login")
_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    auto_error=False,
    description="Вход email/паролем: email — в поле username, client_id/secret не нужны",
)
_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check_api_key(provided: str | None) -> None:
    settings = get_settings()
    if not settings.api_key:
        return
    if provided != settings.api_key:
        _LOG.warning("Запрос отклонён: неверный или отсутствующий X-API-Key")
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Invalid API key")


def verify_api_key(
    x_api_key: str | None = Security(_api_key_scheme),
) -> None:
    """Только заголовок X-API-Key (обычные REST-запросы)."""
    _check_api_key(x_api_key)


def bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    _oauth2_token: str | None = Security(_oauth2_scheme),
) -> str | None:
    """Токен из ``Authorization: Bearer``; другая схема или пустое значение — None.

    ``_oauth2_token`` читает тот же заголовок — он здесь ради схемы в OpenAPI.
    """
    if credentials is None or not credentials.credentials.strip():
        return None
    return credentials.credentials.strip()


def _active_user(db: Session, user_id: int, token_version: int) -> UserRow | None:
    user = db.get(UserRow, user_id)
    # Версия меняется при смене пароля и «выйти везде» — старые токены отозваны.
    if user is None or not user.is_active or token_version != user.token_version:
        return None
    return user


def _user_from_token(db: Session, token: str | None) -> UserRow | None:
    if not token:
        return None
    claims = auth_service.decode_access_token(token)
    if claims is None:
        return None
    return _active_user(db, *claims)


def get_current_user(
    authorization: str | None = Depends(bearer_token),
    db: Session = Depends(get_db),
) -> UserRow:
    """JWT только из ``Authorization: Bearer`` (не из query)."""
    user = _user_from_token(db, authorization)
    if user is None:
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
    return user


@dataclass(frozen=True)
class SseCredentials:
    bearer: str | None
    api_key: str | None
    ticket: str | None


def sse_credentials(
    bearer: str | None = Depends(bearer_token),
    x_api_key: str | None = Security(_api_key_scheme),
    ticket: str | None = Query(
        default=None,
        description="Одноразовый тикет из POST /auth/sse-ticket (EventSource не шлёт заголовки)",
    ),
) -> SseCredentials:
    """Сырые креды SSE-запроса. Проверка — ``authenticate_sse`` в коротком
    ``session_scope`` обработчика: стрим не держит соединение ``get_db``."""
    return SseCredentials(bearer=bearer, api_key=x_api_key, ticket=ticket)


def authenticate_sse(db: Session, creds: SseCredentials) -> UserRow:
    """Пользователь SSE: одноразовый ``?ticket=`` или ``Authorization: Bearer`` + X-API-Key.

    Тикет выдаётся только запросу, прошедшему JWT и X-API-Key, поэтому ключ с ним
    не нужен. JWT и API key в query не принимаются — они оседали бы в логах прокси.
    """
    user = None
    if creds.ticket:
        claims = auth_service.decode_sse_ticket(creds.ticket)
        if claims is not None and sse_tickets.claim_once(claims.jti, claims.expires_at):
            user = _active_user(db, claims.user_id, claims.token_version)
    else:
        _check_api_key(creds.api_key)
        user = _user_from_token(db, creds.bearer)
    if user is None:
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
    return user


def require_admin(user: UserRow = Depends(get_current_user)) -> UserRow:
    """JWT + ``users.is_admin``; иначе 403."""
    if not user.is_admin:
        raise http_error(403, CODE_FORBIDDEN, "Admin only")
    return user
