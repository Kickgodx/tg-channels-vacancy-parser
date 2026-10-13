"""Idempotency-Key для POST /runs."""

from __future__ import annotations

import time

from backend.db import parse_run_repository as pr
from backend.db.session import session_scope
from backend.settings import get_settings


def test_idempotent_post_runs_returns_same_run_id(admin_client, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()

    def fake_run_parse(config, **kwargs):
        from tg_jobs_parser.models.metrics import MetricsSummary
        from tg_jobs_parser.models.parse_result import ParseResult

        return ParseResult(
            run_params={},
            posts_fetched=0,
            vacancies_all=[],
            vacancies_unique=[],
            parse_warnings="",
            metrics=MetricsSummary(),
        )

    monkeypatch.setattr("backend.workers.parse_tasks.run_parse", fake_run_parse)

    headers = {"Idempotency-Key": "parse-key-1"}
    r1 = admin_client.post("/api/v1/runs", json={}, headers=headers)
    assert r1.status_code == 202
    run_id = r1.json()["run_id"]

    # Дождаться завершения, чтобы второй ключ мог создать новый прогон.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        st = admin_client.get(f"/api/v1/runs/{run_id}").json()["status"]
        if st in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.05)

    r2 = admin_client.post("/api/v1/runs", json={}, headers=headers)
    assert r2.status_code == 202
    assert r2.json()["run_id"] == run_id


def test_idempotent_key_replays_conflict_without_second_run(admin_client, monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    with session_scope() as session:
        pr.create_parse_run(session, config_snapshot={})

    headers = {"Idempotency-Key": "conflict-key"}
    r1 = admin_client.post("/api/v1/runs", json={}, headers=headers)
    assert r1.status_code == 409
    # После abandon pending ключ свободен; повтор снова получает CONFLICT_ACTIVE_RUN.
    # Важно: второй queued не создаётся.
    r2 = admin_client.post("/api/v1/runs", json={}, headers=headers)
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "CONFLICT_ACTIVE_RUN"
    with session_scope() as session:
        assert pr.has_active_parse_run(session) is True
