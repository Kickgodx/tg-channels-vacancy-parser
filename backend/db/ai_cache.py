"""Кэш LLM в PostgreSQL (используется из parse_runner / enricher)."""

from __future__ import annotations

from tg_jobs_parser.ai.cache import cache_key as make_cache_key
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import AiConfig
from tg_jobs_parser.utils.logger import get_logger

from backend.db.ai_cache_repository import cache_clear_all, cache_get, cache_put
from backend.db.session import session_scope

_LOG = get_logger(__name__)


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
                return cache_get(session, cache_key=key, ttl_days=self._ttl_days)
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
