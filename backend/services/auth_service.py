"""JWT-auth: хэширование паролей (argon2) и выпуск/проверка access-токенов."""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

_ALGO = "HS256"
_password_hash = PasswordHash.recommended()

_ephemeral_secret: str | None = None
_secret_lock = threading.Lock()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def _jwt_secret() -> str:
    """Секрет подписи из JWT_SECRET; без него — эфемерный (токены сгорают при рестарте)."""
    global _ephemeral_secret
    configured = get_settings().jwt_secret
    if configured:
        return configured
    with _secret_lock:
        if _ephemeral_secret is None:
            _ephemeral_secret = secrets.token_urlsafe(32)
            _LOG.warning(
                "JWT_SECRET не задан — используется эфемерный секрет, "
                "все сессии сбросятся при перезапуске backend."
            )
    return _ephemeral_secret


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=get_settings().jwt_ttl_minutes),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGO)


def decode_access_token(token: str) -> int | None:
    """user_id из токена или None (невалиден/истёк)."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
