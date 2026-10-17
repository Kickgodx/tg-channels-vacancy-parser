"""API HH: admin-only, 503 без токена, skip detail при повторном sync."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path, pg_db):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
telegram:
  telethon_session_file: sessions/tg_jobs.session
sources:
  channels: [test_channel]
search:
  date_from: week_ago
  date_to: today
filters:
  specializations: [Python]
  exclude_keywords: []
  work_formats: [any]
ai:
  enabled: false
fx:
  enabled: false
hh:
  enabled: true
  listing_ttl_days: 40
  search:
    text: "Python AND AQA"
    professional_role: ["124", "96"]
    per_page: 100
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    # Пустые строки перекрывают значения из корневого .env (pydantic env_file).
    monkeypatch.setenv("HH_API_TOKEN", "")
    monkeypatch.setenv("HH_CONTACT_EMAIL", "")
    monkeypatch.setenv("HH_SYNC_JOB_BACKEND", "inprocess")

    from backend.db.session import reset_engine
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app

    with TestClient(app) as c:
        yield c


def _register(client: TestClient, email: str, password: str = "secret1") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _wait_job(client: TestClient, token: str, job_id: str, timeout: float = 10.0) -> dict:
    """Sync идёт фоновым потоком (inprocess): ждём completed/failed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/hh/sync-jobs/{job_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"HH sync {job_id} не завершился за {timeout} с")


def _sync(client: TestClient, token: str, **kwargs) -> dict:
    r = client.post("/api/v1/hh/sync", headers=_auth(token), **kwargs)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    return _wait_job(client, token, r.json()["job_id"])


def test_hh_status_requires_admin(client: TestClient):
    admin = _register(client, "admin@example.com")
    user = _register(client, "user@example.com")
    assert client.get("/api/v1/hh/status", headers=_auth(user)).status_code == 403
    r = client.get("/api/v1/hh/status", headers=_auth(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["token_configured"] is False


def test_hh_sync_503_without_token(client: TestClient):
    admin = _register(client, "admin2@example.com")
    r = client.post("/api/v1/hh/sync", headers=_auth(admin))
    assert r.status_code == 503


def test_hh_sync_skip_detail_on_second_run(client: TestClient, monkeypatch):
    monkeypatch.setenv("HH_API_TOKEN", "test-token")
    monkeypatch.setenv("HH_CONTACT_EMAIL", "dev@example.com")
    from backend.settings import get_settings

    get_settings.cache_clear()

    admin = _register(client, "admin3@example.com")

    short_payload = {
        "id": "100",
        "name": "AQA",
        "alternate_url": "https://hh.ru/vacancy/100",
        "area": {"name": "Москва"},
        "employer": {"id": "1", "name": "ACME"},
        "snippet": {"requirement": "Python", "responsibility": "tests"},
        "work_format": [{"id": "REMOTE"}],
        "salary_range": {"from": 100, "to": 200, "currency": "RUR", "gross": False},
        "experience": {"id": "between1And3", "name": "1-3"},
    }
    detail_payload = {
        **short_payload,
        "description": "<p>Full description with pytest</p>",
        "key_skills": [{"name": "pytest"}],
        "published_at": "2026-10-01T10:00:00+0300",
    }

    search_page = MagicMock()
    search_page.found = 1
    search_page.items = [
        __import__("tg_jobs_parser.hh.models", fromlist=["VacancyShort"]).VacancyShort.model_validate(short_payload)
    ]

    detail = __import__("tg_jobs_parser.hh.models", fromlist=["VacancyDetail"]).VacancyDetail.model_validate(
        detail_payload
    )

    detail_calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

        def iter_vacancy_pages(self, *a, **k):
            yield search_page

        def get_vacancy(self, vacancy_id: str):
            detail_calls["n"] += 1
            assert vacancy_id == "100"
            return detail

    monkeypatch.setattr("backend.services.hh_sync_service.HHClient", FakeClient)

    job1 = _sync(client, admin)
    assert job1["status"] == "completed", job1
    assert job1["progress"]["phase"] == "purge"
    body1 = job1["result"]
    assert body1["details_fetched"] == 1
    assert body1["details_skipped"] == 0
    assert body1["vacancies_inserted"] >= 1
    assert detail_calls["n"] == 1

    job2 = _sync(client, admin)
    assert job2["status"] == "completed", job2
    body2 = job2["result"]
    assert body2["details_skipped"] == 1
    assert body2["details_fetched"] == 0
    assert detail_calls["n"] == 1  # второй sync не дергал detail


def test_hh_sync_passes_period_for_7d(client: TestClient, monkeypatch):
    monkeypatch.setenv("HH_API_TOKEN", "test-token")
    monkeypatch.setenv("HH_CONTACT_EMAIL", "dev@example.com")
    from backend.settings import get_settings

    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

        def iter_vacancy_pages(self, params):
            captured["params"] = params
            return iter([])

    monkeypatch.setattr("backend.services.hh_sync_service.HHClient", FakeClient)

    admin = _register(client, "admin4@example.com")
    job = _sync(client, admin, json={"window": "7d"})
    assert job["status"] == "completed", job
    assert job["window"] == "7d"
    assert job["period"] == 7
    assert job["result"]["period"] == 7
    assert captured["params"].period == 7
    assert captured["params"].date_from is None


def test_hh_sync_range_422_without_dates(client: TestClient, monkeypatch):
    monkeypatch.setenv("HH_API_TOKEN", "test-token")
    monkeypatch.setenv("HH_CONTACT_EMAIL", "dev@example.com")
    from backend.settings import get_settings

    get_settings.cache_clear()

    admin = _register(client, "admin5@example.com")
    r = client.post(
        "/api/v1/hh/sync",
        headers=_auth(admin),
        json={"window": "range"},
    )
    assert r.status_code == 422


def _configure_hh(monkeypatch, client_cls) -> None:
    monkeypatch.setenv("HH_API_TOKEN", "test-token")
    monkeypatch.setenv("HH_CONTACT_EMAIL", "dev@example.com")
    from backend.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("backend.services.hh_sync_service.HHClient", client_cls)


class _EmptyClient:
    def __init__(self, *a, **k):
        pass

    def close(self):
        pass

    def iter_vacancy_pages(self, params):
        return iter([])


def test_hh_sync_conflict_while_job_is_active(client: TestClient, monkeypatch):
    _configure_hh(monkeypatch, _EmptyClient)
    admin = _register(client, "admin6@example.com")
    from backend.db import hh_sync_job_repository as jobs
    from backend.db.session import session_scope

    with session_scope() as session:
        jobs.create_hh_sync_job(session, params={"window": "all"}, requested_by=None)

    r = client.post("/api/v1/hh/sync", headers=_auth(admin))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CONFLICT_ACTIVE_RUN"


def test_hh_sync_idempotency_key_returns_same_job(client: TestClient, monkeypatch):
    _configure_hh(monkeypatch, _EmptyClient)
    admin = _register(client, "admin7@example.com")
    headers = {**_auth(admin), "Idempotency-Key": "sync-1"}

    r1 = client.post("/api/v1/hh/sync", headers=headers, json={"window": "7d"})
    assert r1.status_code == 202, r1.text
    _wait_job(client, admin, r1.json()["job_id"])
    r2 = client.post("/api/v1/hh/sync", headers=headers, json={"window": "7d"})

    assert r2.status_code == 202
    assert r2.json()["job_id"] == r1.json()["job_id"]
    jobs = client.get("/api/v1/hh/sync-jobs", headers=_auth(admin)).json()
    assert [j["id"] for j in jobs] == [r1.json()["job_id"]]


def test_hh_sync_api_error_fails_job(client: TestClient, monkeypatch):
    from tg_jobs_parser.hh.client import HHApiError

    class FailingClient(_EmptyClient):
        def iter_vacancy_pages(self, params):
            raise HHApiError("bad gateway", status_code=502)

    _configure_hh(monkeypatch, FailingClient)
    admin = _register(client, "admin8@example.com")

    job = _sync(client, admin)

    assert job["status"] == "failed"
    assert job["error_message"] == "HH API error: HTTP 502"
    assert job["result"]["searched"] == 0  # частичный итог на момент сбоя
    # Упавший sync не держит активный слот: следующий запускается.
    _configure_hh(monkeypatch, _EmptyClient)
    assert _sync(client, admin)["status"] == "completed"


def test_hh_sync_jobs_are_admin_only(client: TestClient, monkeypatch):
    _configure_hh(monkeypatch, _EmptyClient)
    admin = _register(client, "admin9@example.com")
    user = _register(client, "user9@example.com")
    job = _sync(client, admin)

    assert client.get("/api/v1/hh/sync-jobs", headers=_auth(user)).status_code == 403
    assert client.get(f"/api/v1/hh/sync-jobs/{job['id']}", headers=_auth(user)).status_code == 403
    ticket = client.post("/api/v1/auth/sse-ticket", headers=_auth(user)).json()["ticket"]
    assert client.get(f"/api/v1/hh/sync-jobs/{job['id']}/events?ticket={ticket}").status_code == 403
    assert client.post("/api/v1/hh/sync", headers=_auth(user)).status_code == 403


def test_hh_sync_job_events_stream_status_then_done(client: TestClient, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    _configure_hh(monkeypatch, _EmptyClient)
    admin = _register(client, "admin10@example.com")
    job = _sync(client, admin)

    ticket = client.post("/api/v1/auth/sse-ticket", headers=_auth(admin)).json()["ticket"]
    with client.stream("GET", f"/api/v1/hh/sync-jobs/{job['id']}/events?ticket={ticket}") as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())

    assert "event: status" in text
    assert '"status":"completed"' in text
    assert text.rstrip().endswith("data: {}")  # завершающий event: done
