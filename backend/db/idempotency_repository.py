"""Репозиторий Idempotency-Key."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import IdempotencyKeyRow

DEFAULT_TTL = timedelta(hours=24)
# Короткий TTL для pending: краш до complete не блокирует ключ на сутки.
PENDING_TTL = timedelta(minutes=15)
# status_code sentinel для in-flight резервации
PENDING_STATUS = 0


def get_idempotency(
    session: Session,
    *,
    key: str,
    scope: str,
    actor: str,
) -> IdempotencyKeyRow | None:
    now = datetime.now(UTC)
    row = session.get(IdempotencyKeyRow, (key, scope, actor))
    if row is None:
        return None
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp < now:
        session.delete(row)
        session.flush()
        return None
    return row


def insert_pending_idempotency(
    session: Session,
    *,
    key: str,
    scope: str,
    actor: str,
    request_hash: str | None = None,
    ttl: timedelta = PENDING_TTL,
) -> IdempotencyKeyRow:
    """Вставить pending-резерв. При гонке — IntegrityError (caller делает begin_nested)."""
    now = datetime.now(UTC)
    body: dict[str, Any] = {"_state": "pending"}
    if request_hash:
        body["_request_hash"] = request_hash
    row = IdempotencyKeyRow(
        key=key,
        scope=scope,
        actor=actor,
        status_code=PENDING_STATUS,
        body=body,
        created_at=now,
        expires_at=now + ttl,
    )
    session.add(row)
    session.flush()
    return row


def put_idempotency(
    session: Session,
    *,
    key: str,
    scope: str,
    actor: str,
    status_code: int,
    body: dict[str, Any],
    ttl: timedelta = DEFAULT_TTL,
) -> IdempotencyKeyRow:
    now = datetime.now(UTC)
    existing = session.get(IdempotencyKeyRow, (key, scope, actor))
    if existing is not None:
        existing.status_code = status_code
        existing.body = body
        existing.expires_at = now + ttl
        session.flush()
        return existing
    row = IdempotencyKeyRow(
        key=key,
        scope=scope,
        actor=actor,
        status_code=status_code,
        body=body,
        created_at=now,
        expires_at=now + ttl,
    )
    session.add(row)
    session.flush()
    return row


def delete_idempotency(session: Session, *, key: str, scope: str, actor: str) -> None:
    row = session.get(IdempotencyKeyRow, (key, scope, actor))
    if row is not None:
        session.delete(row)
        session.flush()


def purge_expired_idempotency(session: Session, *, limit: int = 500) -> int:
    now = datetime.now(UTC)
    rows = list(session.scalars(select(IdempotencyKeyRow).where(IdempotencyKeyRow.expires_at < now).limit(limit)))
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)
