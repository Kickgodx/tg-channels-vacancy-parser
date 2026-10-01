"""HTTP-лог: request_id, раздельные REQUEST/RESPONSE, маскирование секретов."""

from __future__ import annotations

import io
import json
import logging
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient

from backend.http_logging import MASK, HttpLoggingMiddleware, render_body
from tg_jobs_parser.utils.logger import RequestIdFilter


def _records(caplog) -> list[dict]:
    out = []
    for r in caplog.records:
        if r.name == "backend.http":
            msg = r.getMessage()
            out.append(json.loads(msg[msg.index("{"):]))
    return out


def _last(caplog, path: str, event: str = "response") -> dict:
    return [
        r for r in _records(caplog) if r["event"] == event and r["path"].startswith(path)
    ][-1]


@pytest.fixture
def mini_client():
    app = FastAPI()
    log = logging.getLogger("backend.test_route")

    @app.post("/echo")
    async def echo(request: Request):
        return await request.json()

    @app.get("/sync-log")
    def sync_log():
        log.info("inside route")
        return {"ok": True}

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

    req = _last(caplog, "/api/v1/auth/register", "request")
    resp = _last(caplog, "/api/v1/auth/register")
    assert req["method"] == "POST" and resp["status"] == 201
    assert req["body"] == {"email": "a@b.c", "password": MASK}
    assert resp["body"]["access_token"] == MASK
    assert resp["body"]["user"]["email"] == "a@b.c"

    api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}", "X-Trace": "t1"}
    )
    req = _last(caplog, "/api/v1/auth/me", "request")
    assert req["headers"]["authorization"] == f"Bearer {MASK}"
    assert req["headers"]["x-trace"] == "t1"
    assert _last(caplog, "/api/v1/auth/me")["body"]["email"] == "a@b.c"

    api_client.get(f"/api/v1/auth/me?access_token={token}&x=1")
    assert _last(caplog, "/api/v1/auth/me?")["path"] == "/api/v1/auth/me?access_token=***&x=1"

    full_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "backend.http")
    assert token not in full_log
    assert "hunter22" not in full_log


def test_request_and_response_are_separate_records_with_same_id(mini_client, caplog):
    caplog.set_level(logging.INFO)
    r = mini_client.post("/echo", json={"a": 1})
    rid = r.headers["x-request-id"]
    assert uuid.UUID(rid)

    recs = [x for x in _records(caplog) if x["request_id"] == rid]
    assert [x["event"] for x in recs] == ["request", "response"]
    req, resp = recs
    assert req["body"] == {"a": 1} and "status" not in req
    assert resp["body"] == {"a": 1} and resp["status"] == 200
    assert resp["headers"]["x-request-id"] == rid

    messages = [x.getMessage() for x in caplog.records if x.name == "backend.http"]
    assert messages[-2].startswith("REQUEST POST /echo {")
    assert messages[-1].startswith("RESPONSE POST /echo 200 ")


def test_incoming_request_id_reused_only_if_uuid(mini_client):
    rid = str(uuid.uuid4())
    assert mini_client.get("/sync-log", headers={"X-Request-ID": rid}).headers["x-request-id"] == rid
    other = mini_client.get("/sync-log", headers={"X-Request-ID": "evil | injected"})
    assert other.headers["x-request-id"] != "evil | injected"
    assert uuid.UUID(other.headers["x-request-id"])


def test_route_logs_carry_request_id_prefix(mini_client):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s | %(request_id_part)s%(message)s"))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        rid = mini_client.get("/sync-log").headers["x-request-id"]
        logging.getLogger("backend.test_route").info("outside request")
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

    lines = stream.getvalue().splitlines()
    assert f"backend.test_route | request_id={rid} | inside route" in lines
    assert any(line.startswith(f"backend.http | request_id={rid} | REQUEST GET /sync-log") for line in lines)
    assert any(line.startswith(f"backend.http | request_id={rid} | RESPONSE GET /sync-log 200") for line in lines)
    assert "backend.test_route | outside request" in lines


def test_noisy_endpoints_and_preflight_are_not_logged(api_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    assert api_client.get("/api/v1/health").status_code == 200
    assert api_client.get("/metrics").status_code == 200
    assert api_client.get("/openapi.json").status_code == 200
    preflight = api_client.options(
        "/api/v1/auth/me",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200
    assert "x-request-id" not in preflight.headers
    assert _records(caplog) == []

    api_client.get("/api/v1/auth/me")
    assert [r["event"] for r in _records(caplog)] == ["request", "response"]


def test_nested_json_redaction(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    payload = {"cfg": {"api_key": "k", "x_api_key": "k2", "max_tokens": 5}, "list": [{"secret": 1}]}
    mini_client.post("/echo", json=payload)
    expected = {"cfg": {"api_key": MASK, "x_api_key": MASK, "max_tokens": 5}, "list": [{"secret": MASK}]}
    assert _last(caplog, "/echo", "request")["body"] == expected
    assert _last(caplog, "/echo")["body"] == expected


def test_non_text_sse_and_attachments_have_no_body(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    for path in ("/bin", "/sse", "/download"):
        mini_client.get(path)
        rec = _last(caplog, path)
        assert rec["status"] == 200
        assert "body" not in rec
        assert "content-type" in rec["headers"]


def test_large_body_is_truncated(mini_client, caplog):
    caplog.set_level(logging.INFO, logger="backend.http")
    mini_client.get("/big")
    body = _last(caplog, "/big")["body"]
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
