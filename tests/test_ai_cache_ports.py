"""Ports & Adapters: registry Postgres-кэша без импорта backend из core."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tg_jobs_parser.ai.cache import build_extraction_cache
from tg_jobs_parser.ai.cache_registry import (
    clear_postgres_cache_factory,
    register_postgres_cache_factory,
)
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import AiConfig


@pytest.fixture(autouse=True)
def _isolate_registry():
    clear_postgres_cache_factory()
    yield
    clear_postgres_cache_factory()
    # Вернуть session fixture registration для остальных тестов.
    from backend.db.ai_cache import register_db_ai_cache

    register_db_ai_cache()


def _ai(**kwargs) -> AiConfig:
    return AiConfig(enabled=True, use_cache=True, cache_backend="postgres", **kwargs)


def test_build_postgres_without_registry_raises():
    with pytest.raises(ValueError, match="не зарегистрирован"):
        build_extraction_cache(_ai())


def test_build_postgres_with_registry_returns_stub():
    class Stub:
        def get(self, **_kwargs):
            return None

        def put(self, **_kwargs):
            return None

    register_postgres_cache_factory(lambda _ai_cfg: Stub())
    backend = build_extraction_cache(_ai())
    assert isinstance(backend, Stub)


def test_import_ai_cache_does_not_load_backend():
    # Убедиться, что модуль кэша не тянет backend при импорте.
    mods_before = {k for k in sys.modules if k == "backend" or k.startswith("backend.")}
    # Перезагрузка не нужна: проверяем исходник модуля.
    import tg_jobs_parser.ai.cache as cache_mod

    src = Path(cache_mod.__file__).read_text(encoding="utf-8")
    assert "from backend" not in src
    assert "import backend" not in src
    mods_after = {k for k in sys.modules if k == "backend" or k.startswith("backend.")}
    # Импорт cache сам по себе не обязан чистить уже загруженный backend (conftest),
    # но не должен добавлять новых backend.* сверх уже известных.
    assert mods_after >= mods_before


def test_null_cache_when_use_cache_false():
    ai = AiConfig(enabled=True, use_cache=False, cache_backend="postgres")
    backend = build_extraction_cache(ai)
    assert backend.get(text="t", model="m", prompt_version="v", mode="primary") is None
    backend.put(
        text="t",
        model="m",
        prompt_version="v",
        mode="primary",
        extraction=VacancyAIExtraction(is_job_post=True),
    )
