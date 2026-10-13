"""Общий OpenRouter-клиент для интерактивного LLM (мэтч / письма).

Один rate limiter на процесс — в отличие от парсинга, где клиент живёт
в рамках прогона. Не закрывать после каждого запроса.
"""

from __future__ import annotations

import threading

from backend.settings import get_settings
from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.ai.rate_limit import OpenRouterRateLimiter
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import AiConfig

_lock = threading.Lock()
_client: OpenRouterClient | None = None
_ai_fingerprint: tuple | None = None


def _fingerprint(ai: AiConfig) -> tuple:
    return (
        ai.base_url,
        ai.requests_per_minute,
        ai.max_retries_on_429,
        ai.max_retries_on_error,
        ai.retry_base_delay_seconds,
        ai.stop_llm_when_rate_limited,
        ai.request_timeout_seconds,
        ai.resolved_match_model(),
        ai.resolved_cover_letter_model(),
    )


def get_interactive_client() -> OpenRouterClient:
    """Процессный клиент с общим ``OpenRouterRateLimiter``."""
    global _client, _ai_fingerprint
    cfg = load_config(get_settings().config_path)
    fp = _fingerprint(cfg.ai)
    with _lock:
        if _client is None or _ai_fingerprint != fp:
            if _client is not None:
                _client.close()
            limiter = OpenRouterRateLimiter(
                requests_per_minute=cfg.ai.requests_per_minute,
                max_retries_on_429=cfg.ai.max_retries_on_429,
                retry_base_delay_seconds=cfg.ai.retry_base_delay_seconds,
                stop_llm_when_rate_limited=cfg.ai.stop_llm_when_rate_limited,
            )
            _client = OpenRouterClient(cfg.ai, rate_limiter=limiter)
            _ai_fingerprint = fp
        return _client


def reset_interactive_client() -> None:
    """Сброс для тестов / смены конфига."""
    global _client, _ai_fingerprint
    with _lock:
        if _client is not None:
            _client.close()
        _client = None
        _ai_fingerprint = None
