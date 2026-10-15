"""Репозиторий refresh-токенов (сессии в httpOnly cookie)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from backend.db.models import RefreshTokenRow


def create_refresh_token(
    session: Session,
    *,
    user_id: int,
    token_hash: str,
    family_id: str,
    token_version: int,
    expires_at: datetime,
) -> RefreshTokenRow:
    row = RefreshTokenRow(
        user_id=user_id,
        token_hash=token_hash,
        family_id=family_id,
        token_version=token_version,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def get_refresh_token_by_hash(session: Session, token_hash: str) -> RefreshTokenRow | None:
    return session.scalar(select(RefreshTokenRow).where(RefreshTokenRow.token_hash == token_hash))


def mark_rotated(session: Session, row_id: int, *, now: datetime) -> bool:
    """Пометить токен ротированным, если его ещё никто не ротировал (атомарно).

    False — гонку выиграл параллельный запрос с тем же токеном (другая вкладка).
    """
    result = session.execute(
        update(RefreshTokenRow)
        .where(
            RefreshTokenRow.id == row_id,
            RefreshTokenRow.rotated_at.is_(None),
            RefreshTokenRow.revoked_at.is_(None),
        )
        .values(rotated_at=now)
    )
    return result.rowcount == 1


def revoke_family(session: Session, family_id: str, *, now: datetime) -> int:
    result = session.execute(
        update(RefreshTokenRow)
        .where(RefreshTokenRow.family_id == family_id, RefreshTokenRow.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result.rowcount


def purge_refresh_tokens(session: Session, *, now: datetime, limit: int = 500) -> int:
    """Удалить истёкшие и отозванные: для проверки они не нужны — не найден = 401.

    Ротированные, но живые остаются до ``expires_at``: по ним ловится повтор украденного токена.
    """
    ids = list(
        session.scalars(
            select(RefreshTokenRow.id)
            .where(or_(RefreshTokenRow.expires_at < now, RefreshTokenRow.revoked_at.is_not(None)))
            .limit(limit)
        )
    )
    if ids:
        session.execute(delete(RefreshTokenRow).where(RefreshTokenRow.id.in_(ids)))
    return len(ids)
