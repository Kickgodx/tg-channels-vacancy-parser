"""SSE-тикеты: одноразовые, короткоживущие, не взаимозаменяемы с access-токеном."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import update

from backend.db import parse_run_repository as pr
from backend.db.models import UserRow
from backend.db.session import session_scope
from backend.http_logging import redact, redact_query
from backend.services import auth_service, sse_tickets


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str = "admin@example.com") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _ticket(client: TestClient, token: str) -> str:
    r = client.post("/api/v1/auth/sse-ticket", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["expires_in"] == int(auth_service.SSE_TICKET_TTL.total_seconds())
    return r.json()["ticket"]


def _completed_run() -> str:
    with session_scope() as session:
        return str(pr.create_parse_run(session, config_snapshot={}, status="completed").id)


def _events(client: TestClient, rid: str, ticket: str) -> int:
    with client.stream("GET", f"/api/v1/runs/{rid}/events?ticket={ticket}") as resp:
        b"".join(resp.iter_bytes())
        return resp.status_code


def test_ticket_requires_login(api_client: TestClient):
    assert api_client.post("/api/v1/auth/sse-ticket").status_code == 401


def test_ticket_is_single_use(api_client: TestClient):
    token = _register(api_client)
    rid = _completed_run()
    ticket = _ticket(api_client, token)

    assert _events(api_client, rid, ticket) == 200
    # Повтор того же URL (из лога прокси или автопереподключение EventSource) — 401.
    assert _events(api_client, rid, ticket) == 401
    assert _events(api_client, rid, _ticket(api_client, token)) == 200


def test_expired_ticket_rejected(api_client: TestClient, monkeypatch):
    token = _register(api_client)
    monkeypatch.setattr(auth_service, "SSE_TICKET_TTL", timedelta(seconds=-1))
    ticket = api_client.post("/api/v1/auth/sse-ticket", headers=_auth(token)).json()["ticket"]
    assert _events(api_client, _completed_run(), ticket) == 401


def test_ticket_and_access_token_not_interchangeable(api_client: TestClient):
    token = _register(api_client)
    ticket = _ticket(api_client, token)

    assert api_client.get("/api/v1/auth/me", headers=_auth(ticket)).status_code == 401
    assert _events(api_client, _completed_run(), token) == 401


def test_ticket_revoked_with_token_version(api_client: TestClient):
    token = _register(api_client)
    ticket = _ticket(api_client, token)
    with session_scope() as session:
        session.execute(update(UserRow).values(token_version=UserRow.token_version + 1))
    assert _events(api_client, _completed_run(), ticket) == 401


def test_ticket_masked_in_logs():
    assert redact_query("ticket=abc&x=1") == "ticket=***&x=1"
    assert redact({"ticket": "abc", "expires_in": 30}) == {"ticket": "***", "expires_in": 30}


def test_claim_once_memory_and_redis(monkeypatch):
    exp = datetime.now(UTC) + timedelta(seconds=30)
    sse_tickets.reset()
    assert sse_tickets.claim_once("j1", exp) is True
    assert sse_tickets.claim_once("j1", exp) is False

    # С Redis — общий для процессов SET NX с TTL тикета.
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(sse_tickets, "_redis", lambda: fake)
    assert sse_tickets.claim_once("j2", exp) is True
    assert sse_tickets.claim_once("j2", exp) is False
    assert 0 < fake.ttl("auth:sse-ticket:j2") <= 30
