"""Idempotency-Key для POST /runs."""

from __future__ import annotations

import time

from backend.db import parse_run_repository as pr
from backend.db.session import session_scope
from backend.settings import get_settings


def _instant_runs(monkeypatch) -> None:
    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()

    def fake_run_parse(config, **kwargs):
        from jobdesk.models.metrics import MetricsSummary
        from jobdesk.models.parse_result import ParseResult

        return ParseResult(
            run_params={},
            posts_fetched=0,
            vacancies_all=[],
            vacancies_unique=[],
            parse_warnings="",
            metrics=MetricsSummary(),
        )

    monkeypatch.setattr("backend.workers.parse_tasks.run_parse", fake_run_parse)


def _wait_finished(client, run_id: str, headers: dict | None = None) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        st = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()["status"]
        if st in ("completed", "failed", "cancelled"):
            return
        time.sleep(0.05)


def test_idempotent_post_runs_returns_same_run_id(admin_client, monkeypatch):
    _instant_runs(monkeypatch)

    headers = {"Idempotency-Key": "parse-key-1"}
    r1 = admin_client.post("/api/v1/runs", json={}, headers=headers)
    assert r1.status_code == 202
    run_id = r1.json()["run_id"]

    # Дождаться завершения, чтобы второй ключ мог создать новый прогон.
    _wait_finished(admin_client, run_id)

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


def test_same_key_of_another_admin_starts_its_own_run(admin_client, monkeypatch):
    from sqlalchemy import update

    from backend.db.models import UserRow

    _instant_runs(monkeypatch)
    r1 = admin_client.post("/api/v1/runs", json={}, headers={"Idempotency-Key": "k1"})
    assert r1.status_code == 202
    _wait_finished(admin_client, r1.json()["run_id"])

    assert (
        admin_client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "secret1"}).status_code
        == 201
    )
    with session_scope() as session:
        session.execute(update(UserRow).where(UserRow.email == "b@example.com").values(is_admin=True))
    token = admin_client.post("/api/v1/auth/login", json={"email": "b@example.com", "password": "secret1"}).json()[
        "access_token"
    ]
    b_headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "k1"}

    r2 = admin_client.post("/api/v1/runs", json={}, headers=b_headers)

    assert r2.status_code == 202, r2.text
    assert r2.json()["run_id"] != r1.json()["run_id"]
