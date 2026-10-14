"""Общий лимит запросов к LLM на Redis (порт ``GlobalRateGate`` ядра).

Счётчик на минутное окно: ``INCR`` атомарен, поэтому API, parse-worker и все
реплики llm-worker делят один ``ai.requests_per_minute``, а не умножают его.
Фиксированное окно допускает пачку до 2×RPM на стыке минут; ровный темп внутри
процесса по-прежнему держит локальный ``OpenRouterRateLimiter``.
"""

from __future__ import annotations

import logging
import math
import time

from backend.settings import get_settings
from tg_jobs_parser.ai.rate_limit import register_global_rate_gate

_LOG = logging.getLogger(__name__)

_KEY_PREFIX = "llm:rate"
_WINDOW_S = 60


class RedisWindowRateGate:
    def __init__(self, connection_factory=None) -> None:
        if connection_factory is None:
            from backend.queue.rq_conn import get_redis_connection

            connection_factory = get_redis_connection
        self._connection_factory = connection_factory

    def wait_seconds(self, key: str, per_minute: float) -> float:
        now = time.time()
        window = int(now // _WINDOW_S)
        redis_key = f"{_KEY_PREFIX}:{key}:{window}"
        pipe = self._connection_factory().pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, _WINDOW_S * 2)
        used, _ = pipe.execute()
        if int(used) <= max(1, math.floor(per_minute)):
            return 0.0
        return max(0.05, (window + 1) * _WINDOW_S - now)


def register_redis_rate_gate() -> bool:
    """Включить общий лимит, если задан ``REDIS_URL``. Вызывать в каждом entrypoint с LLM."""
    if not (get_settings().redis_url or "").strip():
        register_global_rate_gate(None)
        return False
    register_global_rate_gate(RedisWindowRateGate())
    _LOG.info("Общий лимит запросов к LLM: Redis")
    return True
