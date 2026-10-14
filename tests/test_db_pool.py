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


def _settings(size=None, overflow=None):
    return SimpleNamespace(db_pool_size=size, db_max_overflow=overflow)


def test_pool_limits_by_role_and_env_override():
    assert session.pool_limits(_settings()) == (20, 20)
    session.set_pool_role("llm-worker")
    assert session.pool_limits(_settings()) == (2, 2)
    assert session.pool_limits(_settings(size=5)) == (5, 2)
    assert session.pool_limits(_settings(overflow=0)) == (2, 0)
    session.set_pool_role("unknown-role")
    assert session.pool_limits(_settings()) == (20, 20)


def test_worker_role_shrinks_postgres_pool(monkeypatch):
    """Движок создаётся без подключения — проверяем настроенный QueuePool."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/x")
    get_settings.cache_clear()
    session.reset_engine()
    try:
        session.set_pool_role("parse-worker")
        pool = session.get_engine().pool
        assert (pool.size(), pool._max_overflow) == (4, 12)
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


def _fake_engine(dialect: str, log: list[str]):
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect), connect=lambda: _FakeConn(log))


def test_migration_lock_wraps_upgrade_on_postgres():
    log: list[str] = []
    with migrate.migration_lock(_fake_engine("postgresql", log)):
        log.append("upgrade")
    assert log == ["options:AUTOCOMMIT", "pg_advisory_lock", "upgrade", "pg_advisory_unlock"]


def test_migration_lock_released_on_failure():
    log: list[str] = []
    with pytest.raises(RuntimeError), migrate.migration_lock(_fake_engine("postgresql", log)):
        raise RuntimeError("DDL failed")
    assert log[-1] == "pg_advisory_unlock"


def test_migration_lock_is_noop_on_sqlite():
    log: list[str] = []
    with migrate.migration_lock(_fake_engine("sqlite", log)):
        log.append("upgrade")
    assert log == ["upgrade"]
