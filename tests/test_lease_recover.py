"""Lease expiry → recover requeues; live lease → skip."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from rq import Queue

from backend.db import parse_run_repository as pr
from backend.db.session import init_db, reset_engine, session_scope
from backend.settings import get_settings
from backend.workers.recover_parse import recover_stuck_parse_runs


@pytest.fixture(autouse=True)
def _db(monkeypatch, tmp_path):
    db_file = tmp_path / "lease.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-for-tests")
    monkeypatch.setenv("PARSE_JOB_BACKEND", "rq")
    monkeypatch.setenv("PARSE_JOB_LEASE_SECONDS", "60")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def fake_redis(monkeypatch):
    server = fakeredis.FakeStrictRedis()
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    from backend.queue import rq_conn

    get_settings.cache_clear()
    rq_conn.reset_redis_connection()
    monkeypatch.setattr(rq_conn, "get_redis_connection", lambda force_new=False: server)
    yield server
    rq_conn.reset_redis_connection()
    get_settings.cache_clear()


def test_expired_lease_running_is_requeued(fake_redis, monkeypatch):
    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={})
        rid = row.id
        claimed = pr.claim_parse_run(session, rid, lease_owner="t")
        assert claimed is not None
        claimed.lease_until = datetime.now(UTC) - timedelta(seconds=5)

    n = recover_stuck_parse_runs()
    assert n >= 1
    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.status == "queued"
        assert row.rq_job_id is not None
        assert Queue("parse", connection=fake_redis).fetch_job(row.rq_job_id) is not None


def test_live_lease_running_not_recovered(fake_redis):
    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={})
        rid = row.id
        pr.claim_parse_run(session, rid, lease_owner="t")
        # lease_until в будущем (claim только что)

    n = recover_stuck_parse_runs()
    assert n == 0
    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.status == "running"


def test_finalize_after_reclaim_is_noop():
    """Старый worker не может записать terminal после atomic reclaim."""
    from tg_jobs_parser.models.metrics import MetricsSummary
    from tg_jobs_parser.models.parse_result import ParseResult

    with session_scope() as session:
        row = pr.create_parse_run(session, config_snapshot={})
        rid = row.id
        claimed = pr.claim_parse_run(session, rid, lease_owner="old-owner")
        assert claimed is not None
        claimed.lease_until = datetime.now(UTC) - timedelta(seconds=5)

    with session_scope() as session:
        assert pr.reclaim_expired_parse_run(session, rid) == "queued"
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.status == "queued"
        assert row.lease_owner is None

    empty = ParseResult(
        run_params={},
        posts_fetched=0,
        vacancies_all=[],
        vacancies_unique=[],
        parse_warnings="",
        metrics=MetricsSummary(),
    )
    with session_scope() as session:
        assert pr.finalize_parse_run(session, rid, empty, status="completed", lease_owner="old-owner") is False
        assert pr.set_run_status(session, rid, "failed", lease_owner="old-owner") is False
        row = pr.get_parse_run(session, rid)
        assert row is not None
        assert row.status == "queued"


def test_llm_set_status_after_reclaim_is_noop():
    from backend.db import llm_job_repository as lj
    from backend.db.models import LlmJobRow, ResumeRow, UserRow, VacancyRow

    with session_scope() as session:
        user = UserRow(email="lease@test", password_hash="x", is_active=True)
        session.add(user)
        session.flush()
        resume = ResumeRow(user_id=user.id, name="t", text="body")
        session.add(resume)
        session.add(
            VacancyRow(
                id="v-lease-1",
                source_post_key="ch-v-lease-1",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Python",
                cleaned_text="Python",
            )
        )
        session.flush()
        job = LlmJobRow(
            user_id=user.id,
            kind="match",
            status="queued",
            resume_id=resume.id,
            vacancy_id="v-lease-1",
            params={},
        )
        session.add(job)
        session.flush()
        jid = job.id
        claimed = lj.claim_llm_job(session, jid, lease_owner="old-llm")
        assert claimed is not None
        claimed.lease_until = datetime.now(UTC) - timedelta(seconds=5)

    with session_scope() as session:
        assert lj.reclaim_expired_llm_job(session, jid) == "queued"
        assert lj.get_llm_job(session, jid).status == "queued"

    with session_scope() as session:
        assert lj.set_llm_job_status(session, jid, "done", lease_owner="old-llm") is None
        assert lj.get_llm_job(session, jid).status == "queued"
