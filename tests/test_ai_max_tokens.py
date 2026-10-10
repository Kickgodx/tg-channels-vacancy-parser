"""Лимит выходных токенов LLM."""

from unittest.mock import MagicMock

import pytest

from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.config.schema import AppConfig


@pytest.fixture
def search_context() -> SearchContext:
    return SearchContext(
        target_specializations=["Go"],
        target_work_formats=["any"],
        exclude_keywords=[],
        allowed_specializations=["Go"],
        allowed_work_formats=["remote"],
        any_specialization=False,
    )


def test_max_completion_tokens_default():
    cfg = AppConfig()
    assert cfg.ai.max_completion_tokens == 5000


def test_extract_sends_max_tokens(monkeypatch, search_context):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"max_completion_tokens": 5000})})
    client = OpenRouterClient(cfg.ai)
    captured: dict = {}

    def fake_create(kwargs):
        captured.update(kwargs)
        msg = MagicMock()
        msg.content = '{"is_job_post": true, "confidence": 0.9}'
        msg.reasoning = None
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
        return resp

    client._create_completion = fake_create  # type: ignore[method-assign]

    client.extract(text="Go dev remote", search_context=search_context)
    assert captured.get("max_tokens") == 5000
