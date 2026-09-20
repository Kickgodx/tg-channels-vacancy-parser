"""Общие фикстуры тестов: конфиг без сети и без AI."""

from __future__ import annotations

import pytest

# Минимальный конфиг: fx выключен, иначе агрегаты (`_prepare_fx`) на каждый
# вызов ходят за курсами в ЦБ — десятки секунд таймаутов в оффлайн-прогоне.
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

    from backend.settings import get_settings
    from backend.db.session import init_db, reset_engine

    get_settings.cache_clear()
    reset_engine()

    from backend.main import app

    init_db()
    with TestClient(app) as c:
        yield c
    reset_engine()
    get_settings.cache_clear()
