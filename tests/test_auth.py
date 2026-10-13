"""JWT-auth: register/login/me, per-user скоуп резюме и пресетов, legacy-claim."""

from __future__ import annotations

import os

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
""",
        encoding="utf-8",
    )
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    from backend.db.session import init_db, reset_engine
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, email: str, password: str = "secret1"):
    return client.post("/api/v1/auth/register", json={"email": email, "password": password})


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_login_me(client: TestClient):
    r = _register(client, "First@Example.com")
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "first@example.com"
    assert body["user"]["is_admin"] is True  # первый пользователь — админ
    token = body["access_token"]

    r = client.get("/api/v1/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["email"] == "first@example.com"

    # второй пользователь — не админ
    r2 = _register(client, "second@example.com")
    assert r2.status_code == 201
    assert r2.json()["user"]["is_admin"] is False

    # дубликат email → 409
    assert _register(client, "first@example.com").status_code == 409

    # логин: неверный пароль → 401, верный → 200
    bad = client.post(
        "/api/v1/auth/login",
        json={"email": "first@example.com", "password": "wrong-pass"},
    )
    assert bad.status_code == 401
    ok = client.post(
        "/api/v1/auth/login",
        json={"email": "first@example.com", "password": "secret1"},
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]


def test_me_requires_token(client: TestClient):
    assert client.get("/api/v1/auth/me").status_code == 401
    r = client.get("/api/v1/auth/me", headers=_auth("garbage"))
    assert r.status_code == 401


def test_resumes_are_per_user(client: TestClient):
    t1 = _register(client, "a@example.com").json()["access_token"]
    t2 = _register(client, "b@example.com").json()["access_token"]

    assert client.get("/api/v1/resumes").status_code == 401
    assert client.get("/api/v1/resumes", headers=_auth(t1)).json()["items"] == []

    r = client.post(
        "/api/v1/resumes",
        headers=_auth(t1),
        json={"name": "CV A", "text": "python dev"},
    )
    assert r.status_code == 201
    rid = r.json()["id"]

    # второй пользователь не видит чужое резюме
    assert client.get("/api/v1/resumes", headers=_auth(t2)).json()["items"] == []
    assert client.get(f"/api/v1/resumes/{rid}", headers=_auth(t2)).status_code == 404
    assert client.delete(f"/api/v1/resumes/{rid}", headers=_auth(t2)).status_code == 404
    # вакансии с чужим resume_id → 404, без токена → 401
    assert client.get(f"/api/v1/vacancies?resume_id={rid}").status_code == 401
    assert client.get(f"/api/v1/vacancies?resume_id={rid}", headers=_auth(t2)).status_code == 404
    assert client.get(f"/api/v1/vacancies?resume_id={rid}", headers=_auth(t1)).status_code == 200

    assert client.delete(f"/api/v1/resumes/{rid}", headers=_auth(t1)).status_code == 204


def test_resume_update(client: TestClient):
    t1 = _register(client, "a@example.com").json()["access_token"]
    t2 = _register(client, "b@example.com").json()["access_token"]

    rid = client.post(
        "/api/v1/resumes",
        headers=_auth(t1),
        json={"name": "CV", "text": "old text"},
    ).json()["id"]

    assert client.put(f"/api/v1/resumes/{rid}", headers=_auth(t2), json={"name": "x"}).status_code == 404
    assert client.put(f"/api/v1/resumes/{rid}", json={"name": "x"}).status_code == 401

    r = client.put(
        f"/api/v1/resumes/{rid}",
        headers=_auth(t1),
        json={"name": "CV v2", "text": "new text"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "CV v2"
    assert r.json()["text"] == "new text"

    r = client.put(f"/api/v1/resumes/{rid}", headers=_auth(t1), json={"text": "newer text"})
    assert r.status_code == 200
    assert r.json()["name"] == "CV v2"
    assert r.json()["text"] == "newer text"


def test_saved_filters_are_per_user(client: TestClient):
    t1 = _register(client, "a@example.com").json()["access_token"]
    t2 = _register(client, "b@example.com").json()["access_token"]

    r = client.post(
        "/api/v1/filters",
        headers=_auth(t1),
        json={"name": "preset", "params": {"q": "python"}},
    )
    assert r.status_code == 201
    fid = r.json()["id"]

    assert client.get("/api/v1/filters", headers=_auth(t1)).json()["items"][0]["name"] == "preset"
    assert client.get("/api/v1/filters", headers=_auth(t2)).json()["items"] == []
    assert client.delete(f"/api/v1/filters/{fid}", headers=_auth(t2)).status_code == 404
    assert client.delete(f"/api/v1/filters/{fid}", headers=_auth(t1)).status_code == 204


def test_first_user_claims_legacy_rows(client: TestClient):
    from backend.db.models import ResumeRow, SavedFilterRow
    from backend.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        session.add(ResumeRow(name="legacy", text="old resume", user_id=None))
        session.add(SavedFilterRow(name="legacy preset", params={}, user_id=None))
        session.commit()
    finally:
        session.close()

    token = _register(client, "owner@example.com").json()["access_token"]

    resumes = client.get("/api/v1/resumes", headers=_auth(token)).json()["items"]
    assert [r["name"] for r in resumes] == ["legacy"]
    filters = client.get("/api/v1/filters", headers=_auth(token)).json()["items"]
    assert [f["name"] for f in filters] == ["legacy preset"]

    # второй пользователь legacy-строк не видит
    t2 = _register(client, "other@example.com").json()["access_token"]
    assert client.get("/api/v1/resumes", headers=_auth(t2)).json()["items"] == []


def test_registration_closed_for_new_users(client: TestClient, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTRATION_OPEN", "false")
    from backend.settings import get_settings

    get_settings.cache_clear()
    try:
        # первый пользователь регистрируется всегда (bootstrap)
        assert _register(client, "admin@example.com").status_code == 201
        assert _register(client, "second@example.com").status_code == 403
    finally:
        get_settings.cache_clear()


def test_registration_closed_by_default_in_prod(client: TestClient, monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-for-tests-32-chars")
    monkeypatch.delenv("AUTH_REGISTRATION_OPEN", raising=False)
    from backend.settings import get_settings

    get_settings.cache_clear()
    try:
        assert _register(client, "admin@example.com").status_code == 201
        assert _register(client, "second@example.com").status_code == 403
    finally:
        get_settings.cache_clear()
