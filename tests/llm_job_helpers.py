"""Хелперы для тестов фоновых LLM-джоб (202 → poll)."""

from __future__ import annotations

import time
from typing import Any


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def await_llm_job(client, token: str, post_response: Any, *, timeout: float = 5.0) -> dict:
    """Дождаться done/error по 202; при 200 вернуть тело ответа как «готовый» результат."""
    if post_response.status_code == 200:
        return {"status": "done", "body": post_response.json(), "cached_response": True}
    assert post_response.status_code == 202, post_response.text
    job_id = post_response.json()["job_id"]
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/llm-jobs/{job_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("done", "error"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"LLM job {job_id} не завершилась: {last}")
