"""Фоновые LLM-джобы: лимит на пользователя и reuse активной задачи."""

from __future__ import annotations

import inspect
import threading
from datetime import UTC, datetime

from tests.llm_job_helpers import await_llm_job

from backend.api.routes import llm_jobs as llm_jobs_route
from backend.db.models import VacancyRow
from backend.db.session import get_session_factory, session_scope
from backend.services import match_service


def _register(client, email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_vacancy(vacancy_id: str) -> None:
    session = get_session_factory()()
    try:
        session.add(
            VacancyRow(
                id=vacancy_id,
                source_post_key=f"ch-{vacancy_id}",
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


def test_llm_job_reuses_active_and_enforces_per_user_limit(api_client, monkeypatch):
    gate = threading.Event()
    release = threading.Event()

    def _slow(*, resume_id, resume_text, vacancy_id, vacancy_text, **_kwargs):
        gate.set()
        release.wait(timeout=5)
        from backend.db import repository as repo

        with session_scope() as session:
            return repo.upsert_vacancy_match(
                session,
                resume_id=resume_id,
                vacancy_id=vacancy_id,
                overall_score=50,
                confidence=50,
                breakdown={
                    "role_match": 50,
                    "skills_match": 50,
                    "seniority_match": 50,
                    "domain_match": 50,
                    "work_format_match": 50,
                },
                explanation="ok",
                matching_skills=[],
                missing_skills=[],
                strengths=[],
                risks=[],
                model="m",
            )

    monkeypatch.setattr(match_service, "match_single", _slow)
    # default interactive_per_user_limit=2
    token = _register(api_client, "limit@example.com")
    for i in range(3):
        _add_vacancy(f"v-{i}")
    rid = api_client.post(
        "/api/v1/resumes",
        headers=_auth(token),
        json={"name": "CV", "text": "python"},
    ).json()["id"]

    r1 = api_client.post(f"/api/v1/resumes/{rid}/match/v-0", headers=_auth(token))
    assert r1.status_code == 202
    assert gate.wait(timeout=2)

    # тот же pair — reuse job_id
    r1b = api_client.post(f"/api/v1/resumes/{rid}/match/v-0", headers=_auth(token))
    assert r1b.status_code == 202
    assert r1b.json()["job_id"] == r1.json()["job_id"]

    r2 = api_client.post(f"/api/v1/resumes/{rid}/match/v-1", headers=_auth(token))
    assert r2.status_code == 202

    r3 = api_client.post(f"/api/v1/resumes/{rid}/match/v-2", headers=_auth(token))
    assert r3.status_code == 429

    release.set()
    assert await_llm_job(api_client, token, r1)["status"] == "done"
    assert await_llm_job(api_client, token, r2)["status"] == "done"


def test_sse_events_no_long_lived_get_db(api_client, monkeypatch):
    """SSE не держит Depends(get_db); smoke + access_token query."""

    def _fast(*, resume_id, resume_text, vacancy_id, vacancy_text, **_kwargs):
        from backend.db import repository as repo

        with session_scope() as session:
            return repo.upsert_vacancy_match(
                session,
                resume_id=resume_id,
                vacancy_id=vacancy_id,
                overall_score=40,
                confidence=40,
                breakdown={
                    "role_match": 40,
                    "skills_match": 40,
                    "seniority_match": 40,
                    "domain_match": 40,
                    "work_format_match": 40,
                },
                explanation="ok",
                matching_skills=[],
                missing_skills=[],
                strengths=[],
                risks=[],
                model="m",
            )

    monkeypatch.setattr(match_service, "match_single", _fast)
    _add_vacancy("v-sse")
    token = _register(api_client, "sse@example.com")
    rid = api_client.post(
        "/api/v1/resumes",
        headers=_auth(token),
        json={"name": "CV", "text": "python"},
    ).json()["id"]
    r = api_client.post(f"/api/v1/resumes/{rid}/match/v-sse", headers=_auth(token))
    job = await_llm_job(api_client, token, r)
    assert job["status"] == "done"
    job_id = job["id"]

    # сигнатура эндпоинта не тянет get_db / get_current_user (долгая сессия)
    sig = inspect.signature(llm_jobs_route.llm_job_events)
    assert "db" not in sig.parameters
    assert set(sig.parameters) == {"job_id", "creds"}

    with api_client.stream(
        "GET",
        f"/api/v1/llm-jobs/{job_id}/events",
        headers=_auth(token),
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")
    assert "event: status" in body
    assert "done" in body

    ticket = api_client.post("/api/v1/auth/sse-ticket", headers=_auth(token)).json()["ticket"]
    with api_client.stream(
        "GET",
        f"/api/v1/llm-jobs/{job_id}/events?ticket={ticket}",
    ) as resp2:
        assert resp2.status_code == 200
        body2 = b"".join(resp2.iter_bytes()).decode("utf-8", errors="replace")
    assert "event: status" in body2
