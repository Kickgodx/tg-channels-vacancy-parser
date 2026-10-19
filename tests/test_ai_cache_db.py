"""Кэш LLM в Postgres."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.db.ai_cache_repository import (
    cache_clear_all,
    cache_count,
    cache_get,
    cache_put,
)
from tg_jobs_parser.ai.cache import cache_key
from tg_jobs_parser.ai.schema import VacancyAIExtraction


def _sample() -> VacancyAIExtraction:
    return VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["Backend"],
        technologies=["Python"],
        confidence=0.9,
    )


def test_cache_put_get_roundtrip(db_session):
    key = cache_key(text="hello", model="m1", prompt_version="v1", mode="primary")
    cache_put(
        db_session,
        cache_key=key,
        model="m1",
        prompt_version="v1",
        mode="primary",
        extraction=_sample(),
        ttl_days=0,
    )
    db_session.commit()
    got = cache_get(db_session, cache_key=key)
    assert got is not None
    assert got.detected_specializations == ["Backend"]
    assert got.technologies == ["Python"]
    assert cache_count(db_session) == 1


def test_cache_ttl_expires(db_session):
    key = cache_key(text="ttl", model="m1", prompt_version="v1", mode="primary")
    cache_put(
        db_session,
        cache_key=key,
        model="m1",
        prompt_version="v1",
        mode="primary",
        extraction=_sample(),
        ttl_days=1,
    )
    db_session.commit()
    from backend.db.models import AIExtractionCacheRow

    row = db_session.get(AIExtractionCacheRow, key)
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert cache_get(db_session, cache_key=key) is None
    assert cache_count(db_session) == 0


def test_cache_clear_all(db_session):
    key = cache_key(text="x", model="m", prompt_version="v1", mode="assist")
    cache_put(
        db_session,
        cache_key=key,
        model="m",
        prompt_version="v1",
        mode="assist",
        extraction=_sample(),
        ttl_days=90,
    )
    db_session.commit()
    deleted = cache_clear_all(db_session)
    db_session.commit()
    assert deleted == 1
    assert cache_count(db_session) == 0
