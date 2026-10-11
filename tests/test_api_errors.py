"""Структурированный detail ошибок API."""

from __future__ import annotations

from backend.db import parse_run_repository as pr
from backend.db.session import session_scope
from backend.settings import get_settings


def test_conflict_active_run_detail_shape(api_client, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    with session_scope() as session:
        pr.create_parse_run(session, config_snapshot={})

    r = api_client.post("/api/v1/runs", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "CONFLICT_ACTIVE_RUN"
    assert isinstance(detail["message"], str) and detail["message"]
    assert detail["retryable"] is False


def test_http_404_uses_typed_detail(api_client):
    import uuid

    r = api_client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "NOT_FOUND"
    assert detail["retryable"] is False
    assert isinstance(detail["message"], str)
