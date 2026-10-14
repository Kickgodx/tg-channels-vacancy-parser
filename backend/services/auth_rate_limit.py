"""Лимиты ``/auth``: попытки с одного IP и неудачные входы на один email.

Счётчики на фиксированное окно, как у ``llm_rate_gate``: в Redis (если задан
``REDIS_URL`` — общие для всех процессов API), иначе в памяти процесса. Сбой
Redis не блокирует вход — счёт продолжается локально.

Проверка неудачных входов идёт до argon2: перебор пароля упирается в 429, а не
в CPU.
"""

from __future__ import annotations

import logging
import threading
import time

from backend.errors import CODE_RATE_LIMITED, http_error
from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

_KEY_PREFIX = "auth:rate"
_IP_WINDOW_S = 60
_MEMORY_MAX_KEYS = 10_000


class _MemoryCounters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[float, int]] = {}  # key → (expires_at, count)

    def incr(self, key: str, ttl_s: int) -> int:
        now = time.time()
        with self._lock:
            if len(self._counts) >= _MEMORY_MAX_KEYS:
                self._counts = {k: v for k, v in self._counts.items() if v[0] > now}
            expires_at, count = self._counts.get(key, (now + ttl_s, 0))
            self._counts[key] = (expires_at, count + 1)
            return count + 1

    def get(self, key: str) -> int:
        entry = self._counts.get(key)
        return entry[1] if entry and entry[0] > time.time() else 0

    def delete(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._counts.clear()


_memory = _MemoryCounters()


def _redis():
    if not (get_settings().redis_url or "").strip():
        return None
    from backend.queue.rq_conn import get_redis_connection

    return get_redis_connection()


def _incr(key: str, ttl_s: int) -> int:
    try:
        conn = _redis()
        if conn is not None:
            pipe = conn.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_s * 2)
            used, _ = pipe.execute()
            return int(used)
    except Exception as e:
        _LOG.warning("Лимит /auth: Redis недоступен, счёт в памяти процесса: %s", e)
    return _memory.incr(key, ttl_s)


def _get(key: str) -> int:
    try:
        conn = _redis()
        if conn is not None:
            return int(conn.get(key) or 0)
    except Exception as e:
        _LOG.warning("Лимит /auth: Redis недоступен, счёт в памяти процесса: %s", e)
    return _memory.get(key)


def _delete(key: str) -> None:
    try:
        conn = _redis()
        if conn is not None:
            conn.delete(key)
    except Exception as e:
        _LOG.warning("Лимит /auth: Redis недоступен: %s", e)
    _memory.delete(key)


def _window(window_s: int) -> tuple[int, float]:
    """Номер текущего окна и секунд до его конца."""
    now = time.time()
    window = int(now // window_s)
    return window, max(1.0, (window + 1) * window_s - now)


def _too_many(retry_after: float, message: str):
    return http_error(429, CODE_RATE_LIMITED, message, retryable=True, headers={"Retry-After": str(int(retry_after))})


def _failures_key(email: str) -> str:
    window_s = get_settings().auth_failed_login_window_seconds
    window, _ = _window(window_s)
    return f"{_KEY_PREFIX}:fail:{email}:{window}"


def check_ip(ip: str) -> None:
    """Засчитать попытку входа/регистрации с IP; сверх лимита в минуту — 429."""
    limit = get_settings().auth_rate_limit_per_minute
    if limit <= 0:
        return
    window, retry_after = _window(_IP_WINDOW_S)
    if _incr(f"{_KEY_PREFIX}:ip:{ip}:{window}", _IP_WINDOW_S) > limit:
        raise _too_many(retry_after, "Слишком много попыток входа, попробуйте позже")


def check_login_allowed(email: str) -> None:
    """429, если неудачных входов на email за окно уже не меньше лимита."""
    s = get_settings()
    if s.auth_max_failed_logins <= 0:
        return
    if _get(_failures_key(email)) >= s.auth_max_failed_logins:
        _, retry_after = _window(s.auth_failed_login_window_seconds)
        raise _too_many(retry_after, "Слишком много неудачных попыток входа, попробуйте позже")


def record_login_failure(email: str) -> None:
    s = get_settings()
    if s.auth_max_failed_logins > 0:
        _incr(_failures_key(email), s.auth_failed_login_window_seconds)


def reset_login_failures(email: str) -> None:
    if get_settings().auth_max_failed_logins > 0:
        _delete(_failures_key(email))


def reset_auth_rate_limits() -> None:
    """Сбросить счётчики в памяти (тесты)."""
    _memory.clear()
