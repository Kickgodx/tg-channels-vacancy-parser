"""Настройки и RQ-очередь parse-джоб."""

from __future__ import annotations

from backend.settings import get_settings


def test_parse_job_backend_normalized(monkeypatch):
    monkeypatch.setenv("PARSE_JOB_BACKEND", "RQ")
    get_settings.cache_clear()
    assert get_settings().parse_job_backend_normalized == "rq"
    get_settings.cache_clear()
