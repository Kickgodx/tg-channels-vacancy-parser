from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TypeVar

from fastapi import Depends, Query, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import UserRow
from backend.db.session import get_db
from backend.errors import CODE_FORBIDDEN, CODE_NOT_AUTHENTICATED, http_error
from backend.services import api_tokens, auth_service, sse_tickets
from backend.settings import get_settings

__all__ = [
    "CurrentUser",
    "SseCredentials",
    "authenticate_sse",
    "bearer_token",
    "get_current_user",
    "get_db",
    "pat_scope",
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


async def verify_api_key(
    x_api_key: str | None = Security(_api_key_scheme),
) -> None:
    """Только заголовок X-API-Key (обычные REST-запросы)."""
    _check_api_key(x_api_key)


async def bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    _oauth2_token: str | None = Security(_oauth2_scheme),
) -> str | None:
    """Токен из ``Authorization: Bearer``; другая схема или пустое значение — None.

    ``_oauth2_token`` читает тот же заголовок — он здесь ради схемы в OpenAPI.
    """
    if credentials is None or not credentials.credentials.strip():
        return None
    return credentials.credentials.strip()


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Вошедший пользователь запроса — снимок строки ``users`` без хэша пароля.

    Не ORM-сущность: загрузка ``UserRow`` через identity map стоила четверть CPU
    запроса ``/auth/me``. Мутации пользователя загружают ``UserRow`` явно.
    """

    id: int
    email: str
    is_admin: bool
    is_active: bool
    token_version: int
    created_at: datetime
    # jwt — сессия UI (access-токен); pat — персональный токен доступа со scope'ами.
    auth_kind: str = "jwt"
    scopes: tuple[str, ...] = ()


_USER_COLUMNS = (
    UserRow.id,
    UserRow.email,
    UserRow.is_admin,
    UserRow.is_active,
    UserRow.token_version,
    UserRow.created_at,
)


def _active_user(db: Session, user_id: int, token_version: int) -> CurrentUser | None:
    row = db.execute(select(*_USER_COLUMNS).where(UserRow.id == user_id)).one_or_none()
    # Версия меняется при смене пароля и «выйти везде» — старые токены отозваны.
    if row is None or not row.is_active or token_version != row.token_version:
        return None
    return CurrentUser(**row._mapping)


def _user_from_token(db: Session, token: str | None) -> CurrentUser | None:
    if not token:
        return None
    claims = auth_service.decode_access_token(token)
    if claims is None:
        return None
    return _active_user(db, *claims)


_F = TypeVar("_F", bound=Callable)
_PAT_SCOPE_ATTR = "__pat_scope__"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def pat_scope(scope: str) -> Callable[[_F], _F]:
    """Разрешить мутирующий эндпоинт персональному токену с этим scope (ставится под ``@router.*``).

    Без пометки PAT получает только чтение (GET с scope ``read``).
    """

    def mark(fn: _F) -> _F:
        setattr(fn, _PAT_SCOPE_ATTR, scope)
        return fn

    return mark


def _user_from_pat(db: Session, token: str) -> CurrentUser | None:
    claims = api_tokens.authenticate(db, token)
    if claims is None:
        return None
    user = _active_user(db, claims.user_id, claims.token_version)
    if user is None:
        return None
    return replace(user, auth_kind="pat", scopes=claims.scopes)


def _check_pat_scope(request: Request, user: CurrentUser) -> None:
    if request.method in _SAFE_METHODS:
        needed = api_tokens.SCOPE_READ
    else:
        needed = getattr(request.scope.get("endpoint"), _PAT_SCOPE_ATTR, None)
    if needed is None or needed not in user.scopes:
        raise http_error(403, CODE_FORBIDDEN, "Токен доступа не разрешает это действие")


def get_current_user(
    request: Request,
    authorization: str | None = Depends(bearer_token),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """JWT или персональный токен (``tgj_…``) только из ``Authorization: Bearer`` (не из query)."""
    if api_tokens.is_pat(authorization):
        user = _user_from_pat(db, authorization)
        if user is None:
            raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
        _check_pat_scope(request, user)
        return user
    user = _user_from_token(db, authorization)
    if user is None:
        raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
    return user


@dataclass(frozen=True)
class SseCredentials:
    bearer: str | None
    api_key: str | None
    ticket: str | None


async def sse_credentials(
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


def authenticate_sse(db: Session, creds: SseCredentials) -> CurrentUser:
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


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """JWT + ``users.is_admin``; иначе 403. Персональный токен admin-функции не открывает."""
    if not user.is_admin or user.auth_kind != "jwt":
        raise http_error(403, CODE_FORBIDDEN, "Admin only")
    return user
