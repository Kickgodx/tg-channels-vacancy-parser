"""Подключение infra-адаптеров к портам ядра ``tg_jobs_parser``.

Ядро не импортирует ``backend``: кэш LLM в Postgres (``AIExtractionCacheBackend``) и
общий лимит запросов к LLM (``GlobalRateGate``) регистрируются при старте процесса.
Без регистрации кэша ``AIEnrichmentService`` не создаётся (``cache_backend: postgres``),
и LLM-разбор в этом процессе молча не работает. Поэтому каждый entrypoint, где идёт
LLM, — API, ``parse-worker``, ``llm-worker`` — вызывает одну эту функцию, а не
регистрирует адаптеры по отдельности.
"""

from __future__ import annotations


def register_core_ports() -> None:
    """Зарегистрировать Postgres-кэш LLM и общий (Redis) лимит запросов к LLM."""
    from backend.db.ai_cache import register_db_ai_cache
    from backend.services.llm_rate_gate import register_redis_rate_gate

    register_db_ai_cache()
    register_redis_rate_gate()
