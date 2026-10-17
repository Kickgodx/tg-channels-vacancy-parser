"""Репозиторий кэша LLM в PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import AIExtractionCacheRow
from tg_jobs_parser.ai.schema import VacancyAIExtraction


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _expires_at(ttl_days: int) -> datetime | None:
    if ttl_days <= 0:
        return None
    return _utcnow() + timedelta(days=ttl_days)


def cache_get(
    session: Session,
    *,
    cache_key: str,
) -> VacancyAIExtraction | None:
    """Срок жизни проверяется по ``expires_at``, выставленному в ``cache_put``."""
    row = session.get(AIExtractionCacheRow, cache_key)
    if row is None:
        return None
    now = _utcnow()
    if row.expires_at is not None and row.expires_at <= now:
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


def cache_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(AIExtractionCacheRow)) or 0)


def cache_clear_all(session: Session) -> int:
    result = session.execute(delete(AIExtractionCacheRow))
    return int(result.rowcount or 0)


def purge_stale_cache(session: Session, *, current_prompt_version: str, limit: int = 1000) -> int:
    """Удалить до ``limit`` записей, которые больше не прочитаются.

    Это просроченные (иначе уходят только при чтении того же ключа) и записи от
    другой версии промпта: версия входит в ключ, после bump старые ключи не ищутся.
    """
    expired = and_(AIExtractionCacheRow.expires_at.isnot(None), AIExtractionCacheRow.expires_at <= _utcnow())
    stale = (
        select(AIExtractionCacheRow.cache_key)
        .where(or_(expired, AIExtractionCacheRow.prompt_version != current_prompt_version))
        .limit(limit)
    )
    result = session.execute(delete(AIExtractionCacheRow).where(AIExtractionCacheRow.cache_key.in_(stale)))
    return int(result.rowcount or 0)
