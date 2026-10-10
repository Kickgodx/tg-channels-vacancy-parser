from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.db.models import ParseRunRow
from backend.db.session import init_db, reset_engine, session_scope


@pytest.fixture(autouse=True)
def _sqlite_parse_jobs_db(monkeypatch, tmp_path):
    db_file = tmp_path / "parse_jobs.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-for-tests")
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_parse_run_row_accepts_durable_columns():
    with session_scope() as session:
        row = ParseRunRow(
            status="queued",
            config_snapshot={"x": 1},
            rq_job_id="rq-1",
            progress={
                "posts_processed": 1,
                "posts_total": 10,
                "vacancies_found": 0,
                "phase": "processing",
            },
            progress_updated_at=datetime.now(UTC),
            cancel_requested=False,
        )
        session.add(row)
        session.flush()
        rid = row.id
    with session_scope() as session:
        got = session.get(ParseRunRow, rid)
        assert got is not None
        assert got.status == "queued"
        assert got.rq_job_id == "rq-1"
        assert got.progress["posts_processed"] == 1
        assert got.cancel_requested is False
