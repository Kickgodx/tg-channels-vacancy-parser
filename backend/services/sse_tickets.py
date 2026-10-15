"""Одноразовость SSE-тикетов (``POST /auth/sse-ticket`` → ``?ticket=``).

Использованный ``jti`` помечается до истечения тикета: в Redis (если задан
``REDIS_URL`` — общий для всех процессов API, ``SET NX``), иначе в памяти
процесса. Сбой Redis не ломает SSE — проверка продолжается в памяти.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime

from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

_KEY_PREFIX = "auth:sse-ticket"


class _MemoryUsed:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._used: dict[str, float] = {}  # jti → expires_at (epoch)

    def claim(self, jti: str, expires_at: float) -> bool:
        now = time.time()
        with self._lock:
            # Тикеты живут секунды: словарь мал, чистка на каждом вызове дешёвая.
            self._used = {k: exp for k, exp in self._used.items() if exp > now}
            if jti in self._used:
                return False
            self._used[jti] = expires_at
            return True

    def clear(self) -> None:
        with self._lock:
            self._used.clear()


_memory = _MemoryUsed()


def _redis():
    if not (get_settings().redis_url or "").strip():
        return None
    from backend.queue.rq_conn import get_redis_connection

    return get_redis_connection()


def claim_once(jti: str, expires_at: datetime) -> bool:
    """True — тикет предъявлен впервые; False — уже использован."""
    exp = expires_at.timestamp()
    ttl_s = max(1, math.ceil(exp - time.time()))
    try:
        conn = _redis()
        if conn is not None:
            return bool(conn.set(f"{_KEY_PREFIX}:{jti}", 1, nx=True, ex=ttl_s))
    except Exception as e:
        _LOG.warning("SSE-тикет: Redis недоступен, одноразовость — в памяти процесса: %s", e)
    return _memory.claim(jti, exp)


def reset() -> None:
    """Тесты: забыть использованные тикеты процесса."""
    _memory.clear()
