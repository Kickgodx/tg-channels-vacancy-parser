"""Кэши чтения: load_config по mtime, агрегаты дашборда/аналитики с TTL и сбросом при записи."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from backend.db import aggregate_cache, vacancy_repository
from backend.db.aggregate_cache import cached_aggregate, invalidate_aggregate_cache
from backend.db.session import init_db, reset_engine, session_scope
from backend.schemas import VacancyQuery
from backend.settings import get_settings
from tg_jobs_parser.config import loader
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.models.vacancy import Vacancy


def _write(path, threshold: int) -> None:
    path.write_text(f"parser:\n  vacancy_score_threshold: {threshold}\n", encoding="utf-8")


def test_load_config_is_cached_until_file_changes(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    _write(cfg_file, 3)
    calls = []
    real = loader._load_config_uncached
    monkeypatch.setattr(loader, "_load_config_uncached", lambda p: calls.append(p) or real(p))

    assert load_config(cfg_file).parser.vacancy_score_threshold == 3
    assert load_config(cfg_file).parser.vacancy_score_threshold == 3
    assert len(calls) == 1

    _write(cfg_file, 7)
    st = cfg_file.stat()
    os.utime(cfg_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # гарантированно другой mtime
    assert load_config(cfg_file).parser.vacancy_score_threshold == 7
    assert len(calls) == 2


def test_load_config_returns_independent_copies(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    _write(cfg_file, 3)
    first = load_config(cfg_file)
    first.parser.vacancy_score_threshold = 99
    first.sources.channels.append("mutated")
    second = load_config(cfg_file)
    assert second.parser.vacancy_score_threshold == 3
    assert "mutated" not in second.sources.channels


def test_load_config_errors_are_not_cached(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(loader.ConfigLoadError):
        load_config(cfg_file)
    _write(cfg_file, 4)
    st = cfg_file.stat()
    os.utime(cfg_file, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert load_config(cfg_file).parser.vacancy_score_threshold == 4


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'agg.db').as_posix()}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_aggregate_cache_hits_and_keys_by_filters(db):
    calls = []

    def compute():
        calls.append(1)
        return len(calls)

    assert cached_aggregate("m", VacancyQuery(grade="middle"), compute) == 1
    # Пагинация и сортировка на агрегат не влияют — тот же ключ.
    assert cached_aggregate("m", VacancyQuery(grade="middle", page=3, sort_order="asc"), compute) == 1
    assert cached_aggregate("m", VacancyQuery(grade="senior"), compute) == 2
    assert cached_aggregate("m", VacancyQuery(grade="middle"), compute, limit=5) == 3
    assert cached_aggregate("other", VacancyQuery(grade="middle"), compute) == 4


def test_vacancy_upsert_invalidates_aggregates(db):
    calls = []
    cached_aggregate("m", VacancyQuery(), lambda: calls.append(1))
    v = Vacancy(
        id="v1",
        source_post_key="k",
        source_channel="ch",
        source_url="u",
        post_date=datetime.now(UTC),
        raw_text="Python dev",
        cleaned_text="Python dev",
    )
    with session_scope() as session:
        vacancy_repository.upsert_vacancies(session, [v], parse_run_id=None)
    cached_aggregate("m", VacancyQuery(), lambda: calls.append(1))
    assert len(calls) == 2


def test_zero_ttl_disables_cache(db, monkeypatch):
    monkeypatch.setenv("ANALYTICS_CACHE_SECONDS", "0")
    get_settings.cache_clear()
    calls = []
    for _ in range(3):
        cached_aggregate("m", VacancyQuery(), lambda: calls.append(1))
    assert len(calls) == 3


def test_cache_is_bounded(db, monkeypatch):
    monkeypatch.setattr(aggregate_cache, "_MAX_ENTRIES", 3)
    invalidate_aggregate_cache()
    for i in range(10):
        cached_aggregate("m", VacancyQuery(source_channel=f"ch{i}"), lambda: 0)
    assert len(aggregate_cache._entries) == 3
