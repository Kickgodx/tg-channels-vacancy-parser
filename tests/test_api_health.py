"""Тесты FastAPI backend (без Telethon)."""

from __future__ import annotations

from datetime import UTC

import pytest
from fastapi.testclient import TestClient
from tests.conftest import login_as_admin, metrics_text, new_parse_run


@pytest.fixture
def client(monkeypatch, tmp_path, pg_db):
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
    monkeypatch.setenv("CONFIG_PATH", str(cfg))

    from backend.db.session import reset_engine
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app

    with TestClient(app) as c:
        login_as_admin(c)
        yield c


def test_health(client: TestClient):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data == {"status": "ok", "database": True, "config_loaded": True}


def test_health_degraded_when_config_does_not_load(client: TestClient, tmp_path):
    (tmp_path / "config.yaml").write_text("fx:\n  enabeld: false\n", encoding="utf-8")

    r = client.get("/api/v1/health")

    assert r.status_code == 200
    assert r.json() == {"status": "degraded", "database": True, "config_loaded": False}
    assert "enabeld" not in r.text


def test_prometheus_metrics(client: TestClient):
    assert client.get("/api/v1/health").status_code == 200
    body = metrics_text()
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "llm_requests_total" in body
    assert "http_requests_inprogress" in body


def test_list_vacancies_empty(client: TestClient):
    r = client.get("/api/v1/vacancies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_vacancy_metrics_empty(client: TestClient):
    r = client.get("/api/v1/vacancies/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_vacancies"] == 0
    assert body["daily"] == []


def test_ai_cache_clear_empty(client: TestClient):
    r = client.post("/api/v1/ai-cache/clear")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 0
    assert body["remaining"] == 0


def test_ai_cache_stats(client: TestClient):
    r = client.get("/api/v1/ai-cache/stats")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_vacancy_metrics_aggregated(client: TestClient):
    from datetime import datetime

    from backend.db.repository import upsert_vacancies
    from backend.db.session import get_session_factory
    from tg_jobs_parser.models.vacancy import Vacancy
    from tg_jobs_parser.utils.hashing import vacancy_content_id

    text1 = "Python dev remote"
    v = Vacancy(
        id=vacancy_content_id(text1),
        source_post_key="chan-1",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/1",
        post_date=datetime(2026, 3, 1, tzinfo=UTC),
        raw_text=text1,
        cleaned_text=text1,
        detected_specializations=["Python"],
        detected_grade="middle",
        work_format="remote",
        detected_salary_avg=200000.0,
    )
    factory = get_session_factory()
    session = factory()
    try:
        run_id = new_parse_run(session)
        upsert_vacancies(session, [v], parse_run_id=run_id)
        session.commit()
    finally:
        session.close()

    text2 = "Python backend"
    v2 = Vacancy(
        id=vacancy_content_id(text2),
        source_post_key="chan-2",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/2",
        post_date=datetime(2026, 3, 2, tzinfo=UTC),
        raw_text=text2,
        cleaned_text=text2,
        detected_specializations=["Python", "Backend"],
        detected_grade="senior",
        work_format="remote",
        detected_salary_avg=300000.0,
    )
    session = factory()
    try:
        upsert_vacancies(session, [v2], parse_run_id=run_id)
        session.commit()
    finally:
        session.close()

    r = client.get("/api/v1/vacancies/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_vacancies"] == 2
    assert body["vacancies_by_grade"].get("middle") == 1
    assert body["vacancies_by_work_format"].get("remote") == 2
    assert body["vacancies_by_specialization"].get("Python") == 2
    assert body["vacancies_by_specialization"].get("Backend") == 1
    for count in body["vacancies_by_specialization"].values():
        assert count <= body["unique_vacancies"]
    assert len(body["daily"]) >= 1

    r_list = client.get("/api/v1/vacancies?specialization=Backend")
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 1
    assert r_list.json()["items"][0]["id"] == vacancy_content_id(text2)

    r_multi = client.get("/api/v1/vacancies?specialization=Python&specialization=Backend")
    assert r_multi.status_code == 200
    assert r_multi.json()["total"] == 2

    r_metrics = client.get("/api/v1/vacancies/metrics?specialization=Python")
    assert r_metrics.status_code == 200
    assert r_metrics.json()["unique_vacancies"] == 2

    r_salary = client.get("/api/v1/vacancies?has_salary=true")
    assert r_salary.status_code == 200
    assert r_salary.json()["total"] == 2
    assert all(
        item.get("detected_salary_min") is not None
        or item.get("detected_salary_max") is not None
        or item.get("detected_salary_avg") is not None
        for item in r_salary.json()["items"]
    )

    text3 = "Python analyst"
    no_salary = Vacancy(
        id=vacancy_content_id(text3),
        source_post_key="chan-3",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/3",
        post_date=datetime(2026, 3, 3, tzinfo=UTC),
        raw_text=text3,
        cleaned_text=text3,
        detected_specializations=["Python"],
    )
    session = factory()
    try:
        upsert_vacancies(session, [no_salary], parse_run_id=run_id)
        session.commit()
    finally:
        session.close()

    r_all = client.get("/api/v1/vacancies")
    assert r_all.json()["total"] == 3
    r_salary2 = client.get("/api/v1/vacancies?has_salary=true")
    assert r_salary2.json()["total"] == 2
