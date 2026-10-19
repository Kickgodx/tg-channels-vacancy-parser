"""Размер пула по роли процесса и взаимоисключение миграций."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.db import migrate, session
from backend.settings import get_settings


@pytest.fixture(autouse=True)
def _restore_role():
    yield
    session.set_pool_role("api")


def _settings(size=None, overflow=None, workers=1):
    return SimpleNamespace(db_pool_size=size, db_max_overflow=overflow, api_workers=workers)


def test_pool_limits_by_role_and_env_override():
    assert session.pool_limits(_settings()) == (20, 20)
    session.set_pool_role("llm-worker")
    assert session.pool_limits(_settings()) == (2, 2)
    assert session.pool_limits(_settings(size=5)) == (5, 2)
    assert session.pool_limits(_settings(overflow=0)) == (2, 0)
    session.set_pool_role("unknown-role")
    assert session.pool_limits(_settings()) == (20, 20)


def test_pool_timeout_by_role_and_env_override():
    assert session.pool_timeout(SimpleNamespace(db_pool_timeout=None)) == 5.0
    assert session.pool_timeout(SimpleNamespace(db_pool_timeout=12.5)) == 12.5
    session.set_pool_role("llm-worker")
    assert session.pool_timeout(SimpleNamespace(db_pool_timeout=None)) == 30.0


def test_api_engine_waits_for_connection_briefly(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/x")
    monkeypatch.delenv("DB_POOL_TIMEOUT", raising=False)
    get_settings.cache_clear()
    session.reset_engine()
    try:
        assert session.get_engine().pool._timeout == 5.0
    finally:
        session.reset_engine()
        get_settings.cache_clear()


def test_worker_role_shrinks_postgres_pool(monkeypatch):
    """Движок создаётся без подключения — проверяем настроенный QueuePool."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/x")
    get_settings.cache_clear()
    session.reset_engine()
    try:
        session.set_pool_role("parse-worker")
        pool = session.get_engine().pool
        assert (pool.size(), pool._max_overflow) == (4, 12)
        assert pool._timeout == 30.0
    finally:
        session.reset_engine()
        get_settings.cache_clear()


class _FakeConn:
    def __init__(self, log: list[str]):
        self.log = log

    def execution_options(self, **kw):
        self.log.append(f"options:{kw.get('isolation_level')}")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        self.log.append(str(stmt).split("(")[0].replace("SELECT ", ""))


def _fake_engine(log: list[str]):
    return SimpleNamespace(connect=lambda: _FakeConn(log))


def test_migration_lock_wraps_upgrade_on_postgres():
    log: list[str] = []
    with migrate.migration_lock(_fake_engine(log)):
        log.append("upgrade")
    assert log == ["options:AUTOCOMMIT", "pg_advisory_lock", "upgrade", "pg_advisory_unlock"]


def test_migration_lock_released_on_failure():
    log: list[str] = []
    with pytest.raises(RuntimeError), migrate.migration_lock(_fake_engine(log)):
        raise RuntimeError("DDL failed")
    assert log[-1] == "pg_advisory_unlock"


def test_api_pool_is_shared_between_api_workers():
    """Лимит API (20 + 20) — на все процессы вместе."""
    assert session.pool_limits(_settings(workers=4)) == (5, 5)
    assert session.api_thread_limit(_settings(workers=4)) == 10
    assert session.pool_limits(_settings(workers=1)) == (20, 20)
    assert session.api_thread_limit(_settings()) == 40
    # Не меньше одного соединения; overflow 0 остаётся 0.
    assert session.pool_limits(_settings(size=2, overflow=0, workers=8)) == (1, 0)
    assert session.pool_limits(_settings(size=2, overflow=3, workers=8)) == (1, 1)


def test_worker_pool_ignores_api_workers():
    session.set_pool_role("llm-worker")
    assert session.pool_limits(_settings(workers=4)) == (2, 2)
