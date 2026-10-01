"""HTTP-лог: заголовки/тела запросов и ответов, маскирование секретов."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient

from backend.http_logging import MASK, HttpLoggingMiddleware, render_body


def _records(caplog) -> list[dict]:
    out = []
    for r in caplog.records:
        if r.name == "backend.http":
            msg = r.getMessage()
            out.append(json.loads(msg[msg.index("{"):]))
    return out


def _last(caplog, path: str) -> dict:
    return [r for r in _records(caplog) if r["path"].startswith(path)][-1]


@pytest.fixture
def mini_client():
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return await request.json()

    @app.get("/bin")
    def binary():
        return Response(b"\x00\x01\x02", media_type="application/octet-stream")

    @app.get("/big")
    def big():
        return {"items": ["x" * 100] * 100}

    @app.get("/sse")
    def sse():
        return StreamingResponse(iter(["data: 1\n\n"]), media_type="text/event-stream")

    @app.get("/download")
    def download():
        return StreamingResponse(
            iter([b'{"a": 1}']),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="x.json"'},
        )

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    app.add_middleware(HttpLoggingMiddleware, body_limit=500)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_auth_bodies_and_headers_are_redacted(api_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    r = api_client.post(
        "/api/v1/auth/register", json={"email": "a@b.c", "password": "hunter22"}
    )
    assert r.status_code == 201
    token = r.json()["access_token"]

    rec = _last(caplog, "/api/v1/auth/register")
    assert rec["method"] == "POST" and rec["status"] == 201
    assert rec["request"]["body"] == {"email": "a@b.c", "password": MASK}
    assert rec["response"]["body"]["access_token"] == MASK
    assert rec["response"]["body"]["user"]["email"] == "a@b.c"

    api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}", "X-Trace": "t1"}
    )
    rec = _last(caplog, "/api/v1/auth/me")
    assert rec["request"]["headers"]["authorization"] == f"Bearer {MASK}"
    assert rec["request"]["headers"]["x-trace"] == "t1"
    assert rec["response"]["body"]["email"] == "a@b.c"

    api_client.get(f"/api/v1/auth/me?access_token={token}&x=1")
    rec = _last(caplog, "/api/v1/auth/me?")
    assert rec["path"] == "/api/v1/auth/me?access_token=***&x=1"

    full_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "backend.http")
    assert token not in full_log
    assert "hunter22" not in full_log


def test_nested_json_redaction(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    payload = {"cfg": {"api_key": "k", "x_api_key": "k2", "max_tokens": 5}, "list": [{"secret": 1}]}
    mini_client.post("/echo", json=payload)
    rec = _last(caplog, "/echo")
    expected = {"cfg": {"api_key": MASK, "x_api_key": MASK, "max_tokens": 5}, "list": [{"secret": MASK}]}
    assert rec["request"]["body"] == expected
    assert rec["response"]["body"] == expected


def test_non_text_sse_and_attachments_have_no_body(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    for path in ("/bin", "/sse", "/download"):
        mini_client.get(path)
        rec = _last(caplog, path)
        assert rec["status"] == 200
        assert "body" not in rec["response"]
        assert "content-type" in rec["response"]["headers"]


def test_large_body_is_truncated(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    mini_client.get("/big")
    body = _last(caplog, "/big")["response"]["body"]
    assert isinstance(body, str)
    assert body.endswith("bytes]") and "truncated" in body
    assert len(body) < 600


def test_unhandled_error_logged_as_500(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    assert mini_client.get("/boom").status_code == 500
    rec = [r for r in caplog.records if r.name == "backend.http"][-1]
    assert rec.levelno == logging.ERROR
    assert _last(caplog, "/boom")["status"] == 500


def test_truncated_json_falls_back_to_regex_redaction():
    raw = b'{"email": "a@b.c", "password": "p\\"w", "items": [1, 2, 3'
    out = render_body(raw, "application/json", total_bytes=len(raw) + 100, limit=1000)
    assert '"password": "***"' in out
    assert "p\\\"w" not in out
    assert '"email": "a@b.c"' in out


def test_form_body_redaction():
    raw = b"username=u&password=p&grant_type=password"
    out = render_body(
        raw, "application/x-www-form-urlencoded", total_bytes=len(raw), limit=1000
    )
    assert out == "username=u&password=***&grant_type=password"
