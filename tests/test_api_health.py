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
