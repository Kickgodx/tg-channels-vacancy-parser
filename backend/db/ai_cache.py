"""Кэш LLM в PostgreSQL — infra-адаптер для ``AIExtractionCacheBackend``."""

from __future__ import annotations

from backend.db.ai_cache_repository import cache_get, cache_put
from backend.db.session import session_scope
from jobdesk.ai.cache import cache_key as make_cache_key
from jobdesk.ai.cache_registry import register_postgres_cache_factory
from jobdesk.ai.schema import VacancyAIExtraction
from jobdesk.config.schema import AiConfig
from jobdesk.utils.logger import get_logger

_LOG = get_logger(__name__)


def register_db_ai_cache() -> None:
    """Зарегистрировать Postgres-адаптер в core registry (при старте — через ``register_core_ports``)."""
    register_postgres_cache_factory(lambda ai: DbAIExtractionCache(ai))


class DbAIExtractionCache:
    def __init__(self, ai: AiConfig) -> None:
        self._ai = ai
        self._ttl_days = ai.cache_ttl_days

    def get(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
    ) -> VacancyAIExtraction | None:
        key = make_cache_key(text=text, model=model, prompt_version=prompt_version, mode=mode)
        try:
            with session_scope() as session:
                return cache_get(session, cache_key=key)
        except Exception as e:
            _LOG.warning("Кэш LLM (read) недоступен, пропуск: %s", e)
            return None

    def put(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
        extraction: VacancyAIExtraction,
    ) -> None:
        key = make_cache_key(text=text, model=model, prompt_version=prompt_version, mode=mode)
        try:
            with session_scope() as session:
                cache_put(
                    session,
                    cache_key=key,
                    model=model,
                    prompt_version=prompt_version,
                    mode=mode,
                    extraction=extraction,
                    ttl_days=self._ttl_days,
                )
        except Exception as e:
            _LOG.warning("Кэш LLM (write) недоступен, пропуск: %s", e)
