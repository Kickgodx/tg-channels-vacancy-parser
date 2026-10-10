"""API HH: admin-only, 503 без токена, skip detail при повторном sync."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")


@pytest.fixture
def client(monkeypatch, tmp_path):
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
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    # Пустые строки перекрывают значения из корневого .env (pydantic env_file).
    monkeypatch.setenv("HH_API_TOKEN", "")
    monkeypatch.setenv("HH_CONTACT_EMAIL", "")

    from backend.db.session import init_db, reset_engine
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, email: str, password: str = "secret1") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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

    r1 = client.post("/api/v1/hh/sync", headers=_auth(admin))
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["details_fetched"] == 1
    assert body1["details_skipped"] == 0
    assert body1["vacancies_inserted"] >= 1
    assert detail_calls["n"] == 1

    r2 = client.post("/api/v1/hh/sync", headers=_auth(admin))
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
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
    r = client.post(
        "/api/v1/hh/sync",
        headers=_auth(admin),
        json={"window": "7d"},
    )
    assert r.status_code == 200
    assert r.json()["window"] == "7d"
    assert r.json()["period"] == 7
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
