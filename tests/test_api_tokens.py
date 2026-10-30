"""Персональные токены доступа (PAT): выпуск, scope'ы, отказ admin, отзыв."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from backend.db.models import ApiTokenRow
from backend.db.session import session_scope
from backend.services import api_tokens


def _login(client, email="a@example.com") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pat(client, jwt: str, scopes=("read",), **kw) -> dict:
    r = client.post("/api/v1/auth/tokens", headers=_h(jwt), json={"name": "mcp", "scopes": list(scopes), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_list_and_use_token(api_client):
    jwt = _login(api_client)
    created = _pat(api_client, jwt, scopes=["read", "llm"], expires_days=30)
    token = created["token"]
    assert token.startswith("tgj_") and created["prefix"] == token[:12]
    assert created["scopes"] == ["read", "llm"]
    assert created["last_used_at"] is None

    me = api_client.get("/api/v1/auth/me", headers=_h(token))
    assert me.status_code == 200 and me.json()["email"] == "a@example.com"

    listed = api_client.get("/api/v1/auth/tokens", headers=_h(jwt)).json()
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0]
    assert listed[0]["last_used_at"] is not None


def test_read_scope_allows_get_only(api_client):
    jwt = _login(api_client)
    token = _pat(api_client, jwt)["token"]
    assert api_client.get("/api/v1/resumes", headers=_h(token)).status_code == 200
    r = api_client.post("/api/v1/resumes", headers=_h(token), json={"name": "CV", "text": "Python"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FORBIDDEN"
    # LLM-задача без scope llm — тоже 403, до проверки резюме.
    assert api_client.post("/api/v1/resumes/999/match/x", headers=_h(token)).status_code == 403
    assert api_client.post("/api/v1/market-chat/ask", headers=_h(token), json={"question": "?"}).status_code == 403


def test_llm_scope_opens_marked_endpoints_only(api_client):
    jwt = _login(api_client)
    token = _pat(api_client, jwt, scopes=["read", "llm"])["token"]
    # Scope пройден — дальше обычная логика: резюме нет → 404.
    assert api_client.post("/api/v1/resumes/999/match/x", headers=_h(token)).status_code == 404
    assert (
        api_client.post("/api/v1/resumes/999/match-batch", headers=_h(token), json={"vacancy_ids": ["x"]}).status_code
        == 404
    )
    # Не помеченные мутации закрыты и с llm.
    r = api_client.post("/api/v1/resumes", headers=_h(token), json={"name": "CV", "text": "Python"})
    assert r.status_code == 403
    assert api_client.post("/api/v1/auth/sse-ticket", headers=_h(token)).status_code == 403


def test_token_never_opens_admin_endpoints(api_client):
    jwt = _login(api_client)  # первый пользователь — админ
    token = _pat(api_client, jwt, scopes=["read", "llm"])["token"]
    assert api_client.get("/api/v1/runs", headers=_h(jwt)).status_code == 200
    r = api_client.get("/api/v1/runs", headers=_h(token))
    assert r.status_code == 403


def test_token_cannot_manage_tokens(api_client):
    jwt = _login(api_client)
    created = _pat(api_client, jwt, scopes=["read", "llm"])
    token = created["token"]
    assert api_client.post("/api/v1/auth/tokens", headers=_h(token), json={"name": "x"}).status_code == 403
    assert api_client.delete(f"/api/v1/auth/tokens/{created['id']}", headers=_h(token)).status_code == 403


def test_revoked_expired_and_password_change_reject_token(api_client):
    jwt = _login(api_client)
    revoked = _pat(api_client, jwt)
    expired = _pat(api_client, jwt)
    pw = _pat(api_client, jwt)

    assert api_client.delete(f"/api/v1/auth/tokens/{revoked['id']}", headers=_h(jwt)).status_code == 204
    assert api_client.delete(f"/api/v1/auth/tokens/{revoked['id']}", headers=_h(jwt)).status_code == 404
    assert api_client.get("/api/v1/auth/me", headers=_h(revoked["token"])).status_code == 401

    with session_scope() as s:
        s.execute(
            update(ApiTokenRow)
            .where(ApiTokenRow.id == expired["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert api_client.get("/api/v1/auth/me", headers=_h(expired["token"])).status_code == 401

    assert api_client.get("/api/v1/auth/me", headers=_h(pw["token"])).status_code == 200
    r = api_client.post(
        "/api/v1/auth/change-password",
        headers=_h(jwt),
        json={"current_password": "secret1", "new_password": "secret2"},
    )
    assert r.status_code == 200, r.text
    assert api_client.get("/api/v1/auth/me", headers=_h(pw["token"])).status_code == 401
    assert api_client.get("/api/v1/auth/me", headers=_h("tgj_unknown")).status_code == 401


def test_token_sees_only_own_data_and_tokens(api_client):
    jwt_a = _login(api_client)
    api_client.post("/api/v1/resumes", headers=_h(jwt_a), json={"name": "A", "text": "Python"})
    jwt_b = _login(api_client, "b@example.com")
    pat_b = _pat(api_client, jwt_b)
    assert api_client.get("/api/v1/resumes", headers=_h(pat_b["token"])).json()["items"] == []
    assert api_client.get("/api/v1/auth/tokens", headers=_h(jwt_a)).json() == []
    assert api_client.delete(f"/api/v1/auth/tokens/{pat_b['id']}", headers=_h(jwt_a)).status_code == 404


def test_validation_and_limit(api_client, monkeypatch):
    jwt = _login(api_client)
    bad = api_client.post("/api/v1/auth/tokens", headers=_h(jwt), json={"name": "x", "scopes": ["llm"]})
    assert bad.status_code == 422
    blank = api_client.post("/api/v1/auth/tokens", headers=_h(jwt), json={"name": "  "})
    assert blank.status_code == 422
    monkeypatch.setattr(api_tokens, "MAX_ACTIVE_TOKENS", 1)
    _pat(api_client, jwt)
    r = api_client.post("/api/v1/auth/tokens", headers=_h(jwt), json={"name": "second"})
    assert r.status_code == 409
