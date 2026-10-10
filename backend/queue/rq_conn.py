"""Подключение к Redis и очередь RQ ``llm``."""

from __future__ import annotations

import contextlib
from typing import Any

from backend.settings import get_settings

LLM_QUEUE_NAME = "llm"

_connection: Any | None = None


def get_redis_connection(*, force_new: bool = False):
    """``redis.Redis`` из ``REDIS_URL`` (или fakeredis в тестах через monkeypatch)."""
    global _connection
    if _connection is not None and not force_new:
        return _connection
    settings = get_settings()
    url = (settings.redis_url or "").strip()
    if not url:
        raise RuntimeError("REDIS_URL не задан (нужен для LLM_JOB_BACKEND=rq)")
    from redis import Redis

    _connection = Redis.from_url(url)
    return _connection


def reset_redis_connection() -> None:
    global _connection
    if _connection is not None:
        with contextlib.suppress(Exception):
            _connection.close()
    _connection = None


def get_llm_queue():
    from rq import Queue

    return Queue(LLM_QUEUE_NAME, connection=get_redis_connection())


def llm_queue_length() -> int | None:
    """Число jobs в очереди RQ; ``None`` если Redis недоступен / не rq-режим."""
    try:
        settings = get_settings()
        if settings.llm_job_backend_normalized != "rq" or not (settings.redis_url or "").strip():
            return None
        return int(get_llm_queue().count)
    except Exception:
        return None
