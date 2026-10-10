"""Redis Pub/Sub для статуса LLM-джоб (SSE на API)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

_LOG = logging.getLogger(__name__)

CHANNEL_PREFIX = "llm_job:"


def channel_for(job_id: uuid.UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{job_id}"


def publish_llm_job_event(
    job_id: uuid.UUID | str,
    *,
    status: str,
    kind: str | None = None,
    error_message: str | None = None,
) -> None:
    """Лучшее усилие: при отсутствии Redis / ошибке — только debug-лог."""
    try:
        from backend.queue.rq_conn import get_redis_connection
        from backend.settings import get_settings

        if not (get_settings().redis_url or "").strip():
            return
        payload: dict[str, Any] = {
            "job_id": str(job_id),
            "status": status,
        }
        if kind:
            payload["kind"] = kind
        if error_message is not None:
            payload["error_message"] = error_message
        conn = get_redis_connection()
        conn.publish(channel_for(job_id), json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        _LOG.debug("publish_llm_job_event: %s", e)
