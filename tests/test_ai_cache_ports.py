"""Ports & Adapters: registry Postgres-кэша без импорта backend из core."""

from __future__ import annotations

from pathlib import Path

import pytest

from jobdesk.ai.cache import build_extraction_cache
from jobdesk.ai.cache_registry import (
    clear_postgres_cache_factory,
    register_postgres_cache_factory,
)
from jobdesk.ai.schema import VacancyAIExtraction
from jobdesk.config.schema import AiConfig


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
    # Перезагрузка не нужна: проверяем исходник модуля.
    import jobdesk.ai.cache as cache_mod

    src = Path(cache_mod.__file__).read_text(encoding="utf-8")
    assert "from backend" not in src
    assert "import backend" not in src


@pytest.mark.parametrize("worker", ["parse_worker", "llm_worker"])
def test_llm_worker_setup_registers_postgres_cache(worker, monkeypatch):
    """LLM-разбор идёт в RQ-воркерах — без адаптера AI с cache_backend=postgres не создаётся."""
    import importlib

    from jobdesk.ai import cache_registry, rate_limit

    monkeypatch.setattr(rate_limit, "_global_gate", rate_limit._global_gate)
    module = importlib.import_module(f"backend.workers.{worker}")

    module._setup()

    assert cache_registry.get_postgres_cache_factory() is not None
    build_extraction_cache(AiConfig(enabled=True, use_cache=True, cache_backend="postgres"))


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
