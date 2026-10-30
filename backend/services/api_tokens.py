"""Персональные токены доступа (PAT): выпуск, список, отзыв, проверка.

Токен — ``tgj_<token_urlsafe(32)>``, показывается один раз; в БД — sha256 и первые символы
для списка в UI. Scope'ы: ``read`` — GET-эндпоинты залогиненного пользователя, ``llm`` —
эндпоинты, помеченные ``pat_scope("llm")`` (постановка LLM-задач). Admin-эндпоинты PAT не
открывает, а выпускать новые PAT можно только из JWT-сессии.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from backend.db.models import ApiTokenRow

TOKEN_PREFIX = "tgj_"
SCOPE_READ = "read"
SCOPE_LLM = "llm"
SCOPES = (SCOPE_READ, SCOPE_LLM)
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 365
MAX_ACTIVE_TOKENS = 20
# last_used_at обновляется не чаще раза в такой интервал — без записи на каждый запрос.
TOUCH_INTERVAL = timedelta(minutes=1)
_DISPLAY_CHARS = 8


class TooManyTokens(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PatClaims:
    token_id: int
    user_id: int
    token_version: int
    scopes: tuple[str, ...]


def is_pat(token: str | None) -> bool:
    return bool(token) and token.startswith(TOKEN_PREFIX)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _active(now: datetime):
    return (ApiTokenRow.revoked_at.is_(None), ApiTokenRow.expires_at > now)


def create_token(
    session: Session,
    *,
    user_id: int,
    token_version: int,
    name: str,
    scopes: list[str],
    expires_days: int = DEFAULT_TTL_DAYS,
) -> tuple[ApiTokenRow, str]:
    """Новый токен: (строка, значение). Значение больше нигде не хранится."""
    now = datetime.now(UTC)
    active = session.scalar(
        select(func.count()).select_from(ApiTokenRow).where(ApiTokenRow.user_id == user_id, *_active(now))
    )
    if (active or 0) >= MAX_ACTIVE_TOKENS:
        raise TooManyTokens
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiTokenRow(
        user_id=user_id,
        name=name,
        token_hash=_hash(raw),
        prefix=raw[: len(TOKEN_PREFIX) + _DISPLAY_CHARS],
        scopes=[s for s in SCOPES if s in set(scopes)],
        token_version=token_version,
        created_at=now,
        expires_at=now + timedelta(days=expires_days),
    )
    session.add(row)
    session.flush()
    return row, raw


def list_tokens(session: Session, user_id: int) -> list[ApiTokenRow]:
    """Неотозванные токены пользователя (и истёкшие — чтобы было видно, что их пора заменить)."""
    stmt = (
        select(ApiTokenRow)
        .where(ApiTokenRow.user_id == user_id, ApiTokenRow.revoked_at.is_(None))
        .order_by(ApiTokenRow.created_at.desc(), ApiTokenRow.id.desc())
    )
    return list(session.scalars(stmt).all())


def revoke_token(session: Session, user_id: int, token_id: int) -> bool:
    result = session.execute(
        update(ApiTokenRow)
        .where(ApiTokenRow.id == token_id, ApiTokenRow.user_id == user_id, ApiTokenRow.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return result.rowcount > 0


def authenticate(session: Session, raw: str) -> PatClaims | None:
    """Действующий токен → claims (версию и активность пользователя проверяет вызывающий)."""
    now = datetime.now(UTC)
    row = session.execute(
        select(
            ApiTokenRow.id,
            ApiTokenRow.user_id,
            ApiTokenRow.token_version,
            ApiTokenRow.scopes,
            ApiTokenRow.last_used_at,
        ).where(ApiTokenRow.token_hash == _hash(raw), *_active(now))
    ).one_or_none()
    if row is None:
        return None
    if row.last_used_at is None or row.last_used_at < now - TOUCH_INTERVAL:
        session.execute(
            update(ApiTokenRow)
            .where(
                ApiTokenRow.id == row.id,
                or_(ApiTokenRow.last_used_at.is_(None), ApiTokenRow.last_used_at < now - TOUCH_INTERVAL),
            )
            .values(last_used_at=now)
        )
    return PatClaims(row.id, row.user_id, row.token_version, tuple(row.scopes or ()))
