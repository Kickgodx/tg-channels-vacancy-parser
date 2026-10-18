"""RQ-транспорт LLM-джоб (fakeredis) и метрики enqueue."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from prometheus_client import REGISTRY
from rq import Queue, SimpleWorker
from tests.llm_job_helpers import await_llm_job

from backend.db import repository as repo
from backend.db.models import VacancyRow
from backend.db.session import get_session_factory, session_scope
from backend.queue import rq_conn
from backend.services import match_service
from backend.services.llm_job_service import KIND_MATCH, llm_job_runner
from backend.settings import get_settings
from backend.workers.llm_tasks import run_llm_job
from backend.workers.recover import recover_stuck_llm_jobs


def _sample(name: str, labels: dict[str, str]) -> float:
    for family in REGISTRY.collect():
        for s in family.samples:
            if s.name == name and s.labels == labels:
                return float(s.value)
    return 0.0


def _register(client, email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fake_redis(monkeypatch):
    server = fakeredis.FakeStrictRedis()
    monkeypatch.setenv("LLM_JOB_BACKEND", "rq")
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    get_settings.cache_clear()
    rq_conn.reset_redis_connection()
    monkeypatch.setattr(rq_conn, "get_redis_connection", lambda force_new=False: server)
    yield server
    rq_conn.reset_redis_connection()
    get_settings.cache_clear()


def test_rq_enqueue_and_worker_burst(api_client, fake_redis, monkeypatch):
    def _fake(*, resume_id, resume_text, vacancy_id, vacancy_text, **_kwargs):
        from backend.db import repository as repo

        with session_scope() as session:
            return repo.upsert_vacancy_match(
                session,
                resume_id=resume_id,
                vacancy_id=vacancy_id,
                overall_score=66,
                confidence=70,
                breakdown={
                    "role_match": 70,
                    "skills_match": 70,
                    "seniority_match": 70,
                    "domain_match": 70,
                    "work_format_match": 70,
                },
                explanation="ok",
                matching_skills=[],
                missing_skills=[],
                strengths=[],
                risks=[],
                model="m",
            )

    monkeypatch.setattr(match_service, "match_single", _fake)

    session = get_session_factory()()
    try:
        session.add(
            VacancyRow(
                id="vac-rq",
                source_post_key="ch-rq",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Python",
                cleaned_text="Python",
            )
        )
        session.commit()
    finally:
        session.close()

    token = _register(api_client, "rq@example.com")
    rid = api_client.post(
        "/api/v1/resumes",
        headers=_auth(token),
        json={"name": "CV", "text": "python"},
    ).json()["id"]

    before = _sample(
        "llm_job_enqueue_total",
        {"backend": "rq", "kind": "match", "outcome": "queued"},
    )
    r = api_client.post(f"/api/v1/resumes/{rid}/match/vac-rq", headers=_auth(token))
    assert r.status_code == 202, r.text
    assert (
        _sample(
            "llm_job_enqueue_total",
            {"backend": "rq", "kind": "match", "outcome": "queued"},
        )
        == before + 1
    )

    q = Queue("llm", connection=fake_redis)
    assert q.count >= 1
    worker = SimpleWorker([q], connection=fake_redis)
    worker.work(burst=True)

    job = await_llm_job(api_client, token, r)
    assert job["status"] == "done"
    assert job["match"]["overall_score"] == 66


def test_run_llm_job_importable():
    """RQ сериализует dotted path — модуль должен импортироваться."""
    assert callable(run_llm_job)
    assert llm_job_runner is not None


def test_rq_enqueue_persists_rq_job_id(api_client, fake_redis):
    """После RQ enqueue в Postgres сохраняется rq_job_id."""
    session = get_session_factory()()
    try:
        session.add(
            VacancyRow(
                id="vac-rq-id",
                source_post_key="ch-rq-id",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Python",
                cleaned_text="Python",
            )
        )
        session.commit()
    finally:
        session.close()

    token = _register(api_client, "rqid@example.com")
    rid = api_client.post(
        "/api/v1/resumes",
        headers=_auth(token),
        json={"name": "CV", "text": "python"},
    ).json()["id"]

    r = api_client.post(f"/api/v1/resumes/{rid}/match/vac-rq-id", headers=_auth(token))
    assert r.status_code == 202, r.text
    job_id = uuid.UUID(r.json()["job_id"])

    with session_scope() as session:
        row = repo.get_llm_job(session, job_id)
        assert row is not None
        assert row.rq_job_id
        assert Queue("llm", connection=fake_redis).fetch_job(row.rq_job_id) is not None


def test_claim_llm_job_atomic_one_winner(api_client):
    """Два concurrent claim → ровно один победитель."""
    token = _register(api_client, "claim@example.com")
    rid = api_client.post(
        "/api/v1/resumes",
        headers=_auth(token),
        json={"name": "CV", "text": "python"},
    ).json()["id"]
    session = get_session_factory()()
    try:
        session.add(
            VacancyRow(
                id="vac-claim",
                source_post_key="ch-claim",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Python",
                cleaned_text="Python",
            )
        )
        session.commit()
    finally:
        session.close()

    me = api_client.get("/api/v1/auth/me", headers=_auth(token)).json()
    with session_scope() as s:
        job = repo.create_llm_job(
            s,
            user_id=me["id"],
            kind=KIND_MATCH,
            resume_id=rid,
            vacancy_id="vac-claim",
        )
        jid = job.id

    results: list[object] = []
    barrier = threading.Barrier(2)

    def _claim() -> None:
        barrier.wait(timeout=2)
        with session_scope() as s:
            results.append(repo.claim_llm_job(s, jid))

    t1 = threading.Thread(target=_claim)
    t2 = threading.Thread(target=_claim)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1
    assert len(losers) == 1
    with session_scope() as s:
        row = repo.get_llm_job(s, jid)
        assert row is not None
        assert row.status == "running"
        assert row.started_at is not None


def test_recover_skips_live_rq_job(api_client, fake_redis, monkeypatch):
    """Recover не дублирует enqueue, если rq_job_id ещё жив в Redis."""
    monkeypatch.setenv("LLM_JOB_STUCK_MINUTES", "1")
    get_settings.cache_clear()

    token = _register(api_client, "recover@example.com")
    rid = api_client.post(
        "/api/v1/resumes",
        headers=_auth(token),
        json={"name": "CV", "text": "python"},
    ).json()["id"]
    session = get_session_factory()()
    try:
        session.add(
            VacancyRow(
                id="vac-rec",
                source_post_key="ch-rec",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Python",
                cleaned_text="Python",
            )
        )
        session.commit()
    finally:
        session.close()

    me = api_client.get("/api/v1/auth/me", headers=_auth(token)).json()
    q = Queue("llm", connection=fake_redis)
    with session_scope() as s:
        job = repo.create_llm_job(
            s,
            user_id=me["id"],
            kind=KIND_MATCH,
            resume_id=rid,
            vacancy_id="vac-rec",
        )
        jid = job.id
        # «застарелый» queued
        job.created_at = datetime.now(UTC) - timedelta(minutes=30)
        s.flush()

    rq_job = q.enqueue(run_llm_job, str(jid), job_timeout=600)
    with session_scope() as s:
        repo.set_llm_job_rq_job_id(s, jid, str(rq_job.id))

    before = q.count
    n = recover_stuck_llm_jobs()
    assert n == 0
    assert q.count == before

    # без живого RQ job — один re-enqueue
    fake_redis.flushall()
    with session_scope() as s:
        row = repo.get_llm_job(s, jid)
        assert row is not None
        # rq_job_id остаётся, но Job.fetch упадёт → re-enqueue
        assert row.rq_job_id == str(rq_job.id)

    n2 = recover_stuck_llm_jobs()
    assert n2 == 1
    assert Queue("llm", connection=fake_redis).count >= 1
    with session_scope() as s:
        row = repo.get_llm_job(s, jid)
        assert row is not None
        assert row.rq_job_id
        assert row.rq_job_id != str(rq_job.id)
