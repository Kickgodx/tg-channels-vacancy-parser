"""Репозиторий кэша LLM в PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import AIExtractionCacheRow
from tg_jobs_parser.ai.schema import VacancyAIExtraction


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _expires_at(ttl_days: int) -> datetime | None:
    if ttl_days <= 0:
        return None
    return _utcnow() + timedelta(days=ttl_days)


def cache_get(
    session: Session,
    *,
    cache_key: str,
    ttl_days: int,
) -> VacancyAIExtraction | None:
    row = session.get(AIExtractionCacheRow, cache_key)
    if row is None:
        return None
    now = _utcnow()
    if row.expires_at is not None and _as_utc(row.expires_at) <= now:
        session.delete(row)
        return None
    try:
        return VacancyAIExtraction.model_validate(row.payload)
    except ValueError:
        session.delete(row)
        return None


def cache_put(
    session: Session,
    *,
    cache_key: str,
    model: str,
    prompt_version: str,
    mode: str,
    extraction: VacancyAIExtraction,
    ttl_days: int,
) -> None:
    now = _utcnow()
    expires = _expires_at(ttl_days)
    payload = extraction.model_dump(mode="json")
    values = {
        "cache_key": cache_key,
        "model": model,
        "prompt_version": prompt_version,
        "mode": mode,
        "payload": payload,
        "created_at": now,
        "updated_at": now,
        "expires_at": expires,
    }
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = pg_insert(AIExtractionCacheRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["cache_key"],
            set_={
                "model": model,
                "prompt_version": prompt_version,
                "mode": mode,
                "payload": payload,
                "updated_at": now,
                "expires_at": expires,
            },
        )
        session.execute(stmt)
        return

    row = session.get(AIExtractionCacheRow, cache_key)
    if row is None:
        session.add(AIExtractionCacheRow(**values))
    else:
        row.model = model
        row.prompt_version = prompt_version
        row.mode = mode
        row.payload = payload
        row.updated_at = now
        row.expires_at = expires


def cache_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(AIExtractionCacheRow)) or 0)


def cache_clear_all(session: Session) -> int:
    result = session.execute(delete(AIExtractionCacheRow))
    return int(result.rowcount or 0)
