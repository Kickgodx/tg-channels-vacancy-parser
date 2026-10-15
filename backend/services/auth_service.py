"""JWT-auth: хэширование паролей (argon2), access-токены и refresh-сессии."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from backend.db import refresh_token_repository as refresh_repo
from backend.db.models import UserRow
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
                "JWT_SECRET не задан — используется эфемерный секрет, все сессии сбросятся при перезапуске backend."
            )
    return _ephemeral_secret


def create_access_token(user_id: int, token_version: int = 0) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=get_settings().jwt_ttl_minutes),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGO)


def decode_access_token(token: str) -> tuple[int, int] | None:
    """``(user_id, token_version)`` из токена или None (невалиден/истёк).

    Токен без ``ver`` (выпущен до появления отзыва) считается версией 0.
    """
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None
    try:
        return int(payload["sub"]), int(payload.get("ver", 0))
    except (KeyError, TypeError, ValueError):
        return None


# Refresh-сессия: opaque-токен в httpOnly cookie, виден только эндпоинтам /auth.
REFRESH_COOKIE = "tgjp_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
# Вкладки делят один cookie и могут обновиться одновременно: проигравший гонку
# ротации в этом окне получает access без новой ротации, а не отзыв сессии как при краже.
REFRESH_REUSE_GRACE = timedelta(seconds=30)


@dataclass(frozen=True)
class RefreshOutcome:
    user: UserRow
    # Новое значение cookie; None — токен уже ротирован параллельным запросом (grace).
    refresh_token: str | None


def _hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    # SQLite отдаёт naive datetime — в БД всё в UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def issue_refresh_token(session: Session, user: UserRow, *, family_id: str | None = None) -> str:
    """Новый refresh-токен; без ``family_id`` — новая сессия (вход)."""
    raw = secrets.token_urlsafe(32)
    refresh_repo.create_refresh_token(
        session,
        user_id=user.id,
        token_hash=_hash_refresh(raw),
        family_id=family_id or secrets.token_hex(16),
        token_version=user.token_version,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_ttl_days),
    )
    return raw


def rotate_refresh_token(session: Session, raw: str) -> RefreshOutcome | None:
    """Обменять refresh-токен на следующий в той же сессии; None — сессии нет (401)."""
    row = refresh_repo.get_refresh_token_by_hash(session, _hash_refresh(raw))
    now = datetime.now(UTC)
    if row is None or row.revoked_at is not None or _aware(row.expires_at) <= now:
        return None
    user = session.get(UserRow, row.user_id)
    if user is None or not user.is_active or row.token_version != user.token_version:
        return None
    if row.rotated_at is None and refresh_repo.mark_rotated(session, row.id, now=now):
        return RefreshOutcome(user, issue_refresh_token(session, user, family_id=row.family_id))
    session.refresh(row)
    if row.revoked_at is not None:
        return None
    if row.rotated_at is not None and now - _aware(row.rotated_at) <= REFRESH_REUSE_GRACE:
        return RefreshOutcome(user, None)
    refresh_repo.revoke_family(session, row.family_id, now=now)
    # Коммит здесь: вызывающий ответит 401, и get_db откатил бы отзыв вместе с ним.
    session.commit()
    _LOG.warning("Повтор ротированного refresh-токена: сессия отозвана (user id=%s)", user.id)
    return None


def revoke_refresh_session(session: Session, raw: str) -> None:
    """Выход: отозвать сессию, к которой относится токен (неизвестный — игнорировать)."""
    row = refresh_repo.get_refresh_token_by_hash(session, _hash_refresh(raw))
    if row is not None:
        refresh_repo.revoke_family(session, row.family_id, now=datetime.now(UTC))
