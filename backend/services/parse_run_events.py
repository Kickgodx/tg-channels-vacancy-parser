"""Redis Pub/Sub для прогресса parse-run (SSE на API)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

_LOG = logging.getLogger(__name__)

CHANNEL_PREFIX = "parse_run:"


def channel_for(run_id: uuid.UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{run_id}"


def publish_parse_run_event(
    run_id: uuid.UUID | str,
    *,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Лучшее усилие: при отсутствии Redis / ошибке — только debug-лог."""
    try:
        from backend.queue.rq_conn import get_redis_connection
        from backend.settings import get_settings

        if not (get_settings().redis_url or "").strip():
            return
        message: dict[str, Any] = {"event": event, **payload}
        conn = get_redis_connection()
        conn.publish(channel_for(run_id), json.dumps(message, ensure_ascii=False))
    except Exception as e:
        _LOG.debug("publish_parse_run_event: %s", e)
