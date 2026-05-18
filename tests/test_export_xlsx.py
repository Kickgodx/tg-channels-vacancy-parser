"""Экспорт Excel: прогон и глобальный список вакансий."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.test_api_health import client as client_fixture  # noqa: F401


@pytest.fixture
def client(client_fixture):  # noqa: F811
    return client_fixture


def _seed_run_with_vacancy(client: TestClient) -> uuid.UUID:
    from backend.db.session import get_session_factory
    from backend.db.repository import create_parse_run, upsert_vacancies, finalize_parse_run
    from tg_jobs_parser.models.vacancy import Vacancy
    from tg_jobs_parser.models.parse_result import ParseResult
    from tg_jobs_parser.models.metrics import MetricsSummary

    factory = get_session_factory()
    session = factory()
    try:
        row = create_parse_run(session, config_snapshot={})
        run_id = row.id
        v = Vacancy(
            id="export-chan-1",
            source_channel="export_chan",
            source_url="https://t.me/s/export_chan",
            post_url="https://t.me/export_chan/1",
            post_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
            raw_text="Python dev",
            cleaned_text="Python dev",
            detected_specializations=["Python"],
            detected_salary_avg=100000.0,
        )
        upsert_vacancies(session, [v], parse_run_id=run_id)
        finalize_parse_run(
            session,
            run_id,
            ParseResult(
                posts_fetched=1,
                vacancies_unique=[v],
                metrics=MetricsSummary(unique_vacancies=1),
            ),
        )
        session.commit()
        return run_id
    finally:
        session.close()


def test_run_export_xlsx(client: TestClient):
    run_id = _seed_run_with_vacancy(client)
    r = client.get(f"/api/v1/runs/{run_id}/export.xlsx")
    assert r.status_code == 200, r.text[:500]
    assert "spreadsheetml" in r.headers.get("content-type", "")
    assert len(r.content) > 500
    assert r.content[:2] == b"PK"


def test_vacancies_export_xlsx(client: TestClient):
    _seed_run_with_vacancy(client)
    r = client.get("/api/v1/vacancies/export.xlsx")
    assert r.status_code == 200, r.text[:500]
    assert len(r.content) > 500
    assert r.content[:2] == b"PK"


def test_vacancies_export_xlsx_has_salary_filter(client: TestClient):
    _seed_run_with_vacancy(client)
    r = client.get("/api/v1/vacancies/export.xlsx?has_salary=true")
    assert r.status_code == 200
    r0 = client.get("/api/v1/vacancies/export.xlsx?has_salary=false")
    assert r0.status_code == 200
