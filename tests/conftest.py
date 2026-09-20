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
