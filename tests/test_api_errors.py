"""Структурированный detail ошибок API."""

from __future__ import annotations

import uuid

from backend.db import parse_run_repository as pr
from backend.db.session import session_scope
from backend.settings import get_settings


def test_conflict_active_run_detail_shape(admin_client, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    with session_scope() as session:
        pr.create_parse_run(session, config_snapshot={})

    r = admin_client.post("/api/v1/runs", json={})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "CONFLICT_ACTIVE_RUN"
    assert isinstance(detail["message"], str) and detail["message"]
    assert detail["retryable"] is False


def test_http_404_uses_typed_detail(admin_client):
    r = admin_client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "NOT_FOUND"
    assert detail["retryable"] is False
    assert isinstance(detail["message"], str)


def _break_config(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "broken.yaml"
    cfg.write_text("fx:\n  enabeld: false\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    get_settings.cache_clear()


def _assert_config_invalid(r) -> None:
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "CONFIG_INVALID"
    assert detail["retryable"] is False
    assert "fx.enabeld" in detail["message"]


def test_broken_config_is_config_invalid_everywhere(admin_client, monkeypatch, tmp_path):
    _break_config(monkeypatch, tmp_path)

    for url in ("/api/v1/vacancies/metrics", "/api/v1/config", "/api/v1/config/defaults", "/api/v1/fx/rates"):
        _assert_config_invalid(admin_client.get(url))


def test_broken_config_on_run_start_frees_idempotency_key(admin_client, monkeypatch, tmp_path, default_test_config):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    _break_config(monkeypatch, tmp_path)
    headers = {"Idempotency-Key": "broken-config"}

    _assert_config_invalid(admin_client.post("/api/v1/runs", json={}, headers=headers))
    with session_scope() as session:
        assert pr.has_active_parse_run(session) is False

    monkeypatch.setenv("CONFIG_PATH", str(default_test_config))
    get_settings.cache_clear()
    monkeypatch.setattr("backend.services.run_service.start_parse_run", lambda *_a, **_k: uuid.uuid4())
    assert admin_client.post("/api/v1/runs", json={}, headers=headers).status_code == 202


def test_run_override_model_outside_catalog_is_still_400(admin_client):
    r = admin_client.post("/api/v1/runs", json={"ai": {"model": "evil/unknown-model"}})

    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "VALIDATION"
