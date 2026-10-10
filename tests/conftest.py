"""Общие фикстуры тестов: конфиг без сети и без AI."""

from __future__ import annotations

import pytest

# Минимальный конфиг: fx выключен (upsert/apply и startup-sync не тянут ЦБ в оффлайне).
_TEST_CONFIG = """
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
"""


@pytest.fixture(scope="session", autouse=True)
def default_test_config(tmp_path_factory):
    """CONFIG_PATH по умолчанию для всех тестов; свой monkeypatch.setenv перекрывает."""
    cfg = tmp_path_factory.mktemp("config") / "config.yaml"
    cfg.write_text(_TEST_CONFIG, encoding="utf-8")

    from backend.settings import get_settings

    mp = pytest.MonkeyPatch()
    mp.setenv("CONFIG_PATH", str(cfg))
    get_settings.cache_clear()
    yield cfg
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture
def api_client(monkeypatch, tmp_path, default_test_config):
    """TestClient на своей SQLite-базе; схема — через alembic (`init_db`)."""
    from fastapi.testclient import TestClient

    db_file = tmp_path / "api.db"
    monkeypatch.setenv("CONFIG_PATH", str(default_test_config))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-for-tests")

    from backend.db.session import init_db, reset_engine
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app
    from backend.queue.rq_conn import reset_redis_connection
    from backend.services.interactive_llm import reset_interactive_client
    from backend.services.llm_job_service import llm_job_runner

    monkeypatch.setenv("LLM_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()

    init_db()
    with TestClient(app) as c:
        yield c
    llm_job_runner.reset()
    reset_interactive_client()
    reset_redis_connection()
    reset_engine()
    get_settings.cache_clear()
