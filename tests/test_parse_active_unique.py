"""Partial UNIQUE: не больше одного active parse_run."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from backend.db import parse_run_repository as pr
from backend.db.session import init_db, reset_engine, session_scope


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    db_file = tmp_path / "active_unique.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-for-tests")
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_second_queued_parse_run_violates_unique():
    with session_scope() as session:
        pr.create_parse_run(session, config_snapshot={"a": 1})

    with pytest.raises(IntegrityError), session_scope() as session:
        pr.create_parse_run(session, config_snapshot={"b": 2})


def test_completed_then_new_queued_ok():
    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={})
        rid = row.id
    with session_scope() as session:
        pr.set_run_status(session, rid, "completed")
    with session_scope() as session:
        pr.create_parse_run(session, config_snapshot={"ok": True})
        assert pr.has_active_parse_run(session) is True
