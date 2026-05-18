"""Тесты FastAPI backend (без Telethon)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# SQLite для тестов без Docker
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+pysqlite:///:memory:",
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
telegram:
  telethon_session_file: sessions/tg_jobs.session
sources:
  channels: [test_channel]
search:
  date_from: "2026-01-01"
  date_to: today
filters:
  specializations: [Python]
  exclude_keywords: []
  work_formats: [any]
ai:
  enabled: false
""",
        encoding="utf-8",
    )
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")

    from backend.settings import get_settings
    from backend.db.session import init_db, reset_engine

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("ok", "degraded")
    assert "database" in data


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


def test_vacancy_metrics_aggregated(client: TestClient):
    import uuid
    from datetime import datetime, timezone

    from backend.db.session import get_session_factory
    from backend.db.repository import upsert_vacancies
    from tg_jobs_parser.models.vacancy import Vacancy

    run_id = uuid.uuid4()
    v = Vacancy(
        id="chan-1",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/1",
        post_date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        raw_text="Python dev remote",
        cleaned_text="Python dev remote",
        detected_specializations=["Python"],
        detected_grade="middle",
        work_format="remote",
        detected_salary_avg=200000.0,
    )
    factory = get_session_factory()
    session = factory()
    try:
        upsert_vacancies(session, [v], parse_run_id=run_id)
        session.commit()
    finally:
        session.close()

    r = client.get("/api/v1/vacancies/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_vacancies"] == 1
    assert body["vacancies_by_grade"].get("middle") == 1
    assert body["vacancies_by_work_format"].get("remote") == 1
    assert body["vacancies_by_specialization"].get("Python") == 1
    assert len(body["daily"]) >= 1
