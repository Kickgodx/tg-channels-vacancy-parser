from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from rq import Queue

from backend.db import parse_run_repository as pr
from backend.db.models import ParseRunRow
from backend.db.session import init_db, reset_engine, session_scope
from backend.settings import get_settings
from backend.workers.parse_tasks import run_parse_job
from backend.workers.recover_parse import recover_stuck_parse_runs


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


def test_parse_job_backend_normalized(monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "RQ")
    get_settings.cache_clear()
    assert get_settings().parse_job_backend_normalized == "rq"
    get_settings.cache_clear()


def test_start_parse_run_inprocess_completes(api_client, monkeypatch):
    import time

    def fake_run_parse(config, **kwargs):
        from tg_jobs_parser.models.metrics import MetricsSummary
        from tg_jobs_parser.models.parse_result import ParseResult
        from tg_jobs_parser.parse_progress import ParseProgressSnapshot

        cb = kwargs.get("progress_callback")
        if cb:
            cb(ParseProgressSnapshot(1, 1, 0))
        return ParseResult(
            run_params={},
            posts_fetched=1,
            vacancies_all=[],
            vacancies_unique=[],
            parse_warnings="",
            metrics=MetricsSummary(),
        )

    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    monkeypatch.setattr("backend.workers.parse_tasks.run_parse", fake_run_parse)

    r = api_client.post("/api/v1/runs", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    run_id = body["run_id"]

    deadline = time.monotonic() + 10.0
    status = None
    while time.monotonic() < deadline:
        got = api_client.get(f"/api/v1/runs/{run_id}")
        assert got.status_code == 200
        status = got.json()["status"]
        if status in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert status == "completed"


def test_second_post_runs_while_queued_returns_409(api_client, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    with session_scope() as session:
        pr.create_parse_run(session, config_snapshot={})
        assert pr.has_active_parse_run(session) is True

    r = api_client.post("/api/v1/runs", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "CONFLICT_ACTIVE_RUN"
    assert "прогон" in detail["message"].lower()
    assert detail["retryable"] is False


def test_enqueue_failure_marks_run_failed(api_client, monkeypatch):
    from backend.config_builder import build_config
    from backend.schemas import RunCreateRequest
    from backend.services import run_service

    monkeypatch.setenv("PARSE_JOB_BACKEND", "rq")
    get_settings.cache_clear()

    class _BoomQueue:
        def enqueue(self, *args, **kwargs):
            raise RuntimeError("redis down")

    monkeypatch.setattr("backend.queue.rq_conn.get_parse_queue", lambda: _BoomQueue())

    settings = get_settings()
    config = build_config(settings.config_path, RunCreateRequest())
    with pytest.raises(RuntimeError, match="redis down"):
        run_service.start_parse_run(config, RunCreateRequest())

    with session_scope() as session:
        rows = pr.list_parse_runs(session, limit=10)
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].error_message == "Failed to enqueue job"
        assert pr.has_active_parse_run(session) is False

    # Следующий POST не заблокирован «зависшим» queued.
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()

    def fake_run_parse(config, **kwargs):
        from tg_jobs_parser.models.metrics import MetricsSummary
        from tg_jobs_parser.models.parse_result import ParseResult

        return ParseResult(
            run_params={},
            posts_fetched=0,
            vacancies_all=[],
            vacancies_unique=[],
            parse_warnings="",
            metrics=MetricsSummary(),
        )

    monkeypatch.setattr("backend.workers.parse_tasks.run_parse", fake_run_parse)
    r2 = api_client.post("/api/v1/runs", json={})
    assert r2.status_code == 202


def test_cancel_queued_run(api_client):
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}).id
    r = api_client.post(f"/api/v1/runs/{rid}/cancel")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    got = api_client.get(f"/api/v1/runs/{rid}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "cancelled"
    assert body["cancel_requested"] is True


def test_cancel_running_sets_flag():
    from backend.services import run_service

    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}).id
    with session_scope() as session:
        claimed = pr.claim_parse_run(session, rid)
        assert claimed is not None and claimed.status == "running"
    assert run_service.cancel_run(rid) is True
    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.cancel_requested is True
        assert row.status == "running"


def test_reset_parse_run_to_queued_with_cancel_stays_cancelled():
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}).id
    with session_scope() as session:
        claimed = pr.claim_parse_run(session, rid)
        assert claimed is not None
        pr.request_parse_run_cancel(session, rid)
    with session_scope() as session:
        pr.reset_parse_run_to_queued(session, rid)
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.cancel_requested is True
        assert row.status == "cancelled"
        assert row.finished_at is not None


def test_recover_stuck_running(fake_redis_for_parse, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "rq")
    monkeypatch.setenv("PARSE_JOB_STUCK_MINUTES", "1")
    get_settings.cache_clear()

    old = datetime.now(UTC) - timedelta(minutes=30)
    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={})
        rid = row.id
        claimed = pr.claim_parse_run(session, rid)
        assert claimed is not None
        claimed.created_at = old
        claimed.started_at = old
        claimed.progress_updated_at = old
        claimed.lease_until = old
        session.flush()

    q = Queue("parse", connection=fake_redis_for_parse)
    before = q.count
    n = recover_stuck_parse_runs()
    assert n == 1
    assert q.count == before + 1

    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.status == "queued"
        assert row.rq_job_id
        assert row.started_at is None


def test_recover_skips_cancel_requested(fake_redis_for_parse, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "rq")
    monkeypatch.setenv("PARSE_JOB_STUCK_MINUTES", "1")
    get_settings.cache_clear()

    old = datetime.now(UTC) - timedelta(minutes=30)
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}).id
        claimed = pr.claim_parse_run(session, rid)
        assert claimed is not None
        claimed.created_at = old
        claimed.started_at = old
        claimed.progress_updated_at = old
        claimed.lease_until = old
        pr.request_parse_run_cancel(session, rid)

    q = Queue("parse", connection=fake_redis_for_parse)
    before = q.count
    n = recover_stuck_parse_runs()
    assert n == 0
    assert q.count == before

    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.status == "cancelled"
        assert row.cancel_requested is True


def test_recover_skips_live_rq_job(fake_redis_for_parse, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "rq")
    monkeypatch.setenv("PARSE_JOB_STUCK_MINUTES", "1")
    get_settings.cache_clear()

    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={})
        rid = row.id
        row.created_at = datetime.now(UTC) - timedelta(minutes=30)
        session.flush()

    q = Queue("parse", connection=fake_redis_for_parse)
    rq_job = q.enqueue(run_parse_job, str(rid), job_timeout=600)
    with session_scope() as session:
        pr.set_parse_run_rq_job_id(session, rid, str(rq_job.id))

    before = q.count
    n = recover_stuck_parse_runs()
    assert n == 0
    assert q.count == before

    fake_redis_for_parse.flushall()
    n2 = recover_stuck_parse_runs()
    assert n2 == 1
    assert Queue("parse", connection=fake_redis_for_parse).count >= 1
    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.rq_job_id
        assert row.rq_job_id != str(rq_job.id)
