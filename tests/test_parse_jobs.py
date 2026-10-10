from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import fakeredis
import pytest

from backend.db import parse_run_repository as pr
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


def test_claim_parse_run_only_once():
    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={"a": 1})
        rid = row.id
    with session_scope() as session:
        first = pr.claim_parse_run(session, rid)
        assert first is not None and first.status == "running"
    with session_scope() as session:
        second = pr.claim_parse_run(session, rid)
        assert second is None


def test_has_active_parse_run():
    with session_scope() as session:
        assert pr.has_active_parse_run(session) is False
        pr.create_parse_run(session, config_snapshot={})
        assert pr.has_active_parse_run(session) is True


def test_patch_run_progress_sets_heartbeat():
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}).id
    snap = {"posts_processed": 5, "posts_total": 10, "vacancies_found": 1, "phase": "processing"}
    with session_scope() as session:
        pr.patch_run_progress(session, rid, snap)
        row = pr.get_parse_run(session, rid)
        assert row.progress["posts_processed"] == 5
        assert row.progress_updated_at is not None


def test_request_cancel_queued_marks_cancelled():
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}).id
    with session_scope() as session:
        row = pr.request_parse_run_cancel(session, rid)
        assert row is not None
        assert row.status == "cancelled"
        assert row.cancel_requested is True


@pytest.fixture
def fake_redis_for_parse(monkeypatch):
    server = fakeredis.FakeStrictRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    from backend.queue import rq_conn
    from backend.settings import get_settings

    get_settings.cache_clear()
    rq_conn.reset_redis_connection()
    monkeypatch.setattr(rq_conn, "get_redis_connection", lambda force_new=False: server)
    yield server
    rq_conn.reset_redis_connection()
    get_settings.cache_clear()


def test_snapshot_to_progress_dict_slim():
    from backend.services.parse_progress_serialize import snapshot_to_progress_dict
    from tg_jobs_parser.parse_progress import ParseProgressSnapshot

    snap = ParseProgressSnapshot(posts_processed=2, posts_total=9, vacancies_found=0, phase="ai")
    d = snapshot_to_progress_dict(snap)
    assert d["posts_processed"] == 2
    assert d["phase"] == "ai"
    assert "vacancies_preview" in d
    assert d["vacancies_preview"] == []


def test_publish_parse_run_event(fake_redis_for_parse, monkeypatch):
    from backend.services.parse_run_events import channel_for, publish_parse_run_event

    rid = uuid.uuid4()
    pubsub = fake_redis_for_parse.pubsub()
    pubsub.subscribe(channel_for(rid))
    pubsub.get_message(timeout=1)

    publish_parse_run_event(rid, event="progress", payload={"posts_processed": 1})

    data = None
    for _ in range(20):
        msg = pubsub.get_message(timeout=1)
        if msg and msg.get("type") == "message":
            data = json.loads(msg["data"])
            break
    assert data is not None
    assert data["event"] == "progress"
    assert data["posts_processed"] == 1
