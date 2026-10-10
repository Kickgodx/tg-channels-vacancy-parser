"""Регистрация infra-адаптера Postgres-кэша AI (Ports & Adapters).

Core (`build_extraction_cache`) не импортирует ``backend``: фабрику регистрирует
приложение при старте (FastAPI lifespan / CLI).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tg_jobs_parser.config.schema import AiConfig

if TYPE_CHECKING:
    from tg_jobs_parser.ai.cache import AIExtractionCacheBackend

    PostgresCacheFactory = Callable[[AiConfig], AIExtractionCacheBackend]
else:
    PostgresCacheFactory = Callable[[AiConfig], Any]

_postgres_factory: PostgresCacheFactory | None = None


def register_postgres_cache_factory(factory: PostgresCacheFactory) -> None:
    """Зарегистрировать фабрику адаптера ``DbAIExtractionCache`` (или теста)."""
    global _postgres_factory
    _postgres_factory = factory


def get_postgres_cache_factory() -> PostgresCacheFactory | None:
    return _postgres_factory


def clear_postgres_cache_factory() -> None:
    """Сброс для тестов."""
    global _postgres_factory
    _postgres_factory = None
