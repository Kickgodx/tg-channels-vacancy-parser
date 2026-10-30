"""Параметры рассуждений OpenRouter: ``match_reasoning_effort`` и исчерпание max_tokens."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from jobdesk.ai.clients.openrouter import OpenRouterClient
from jobdesk.config.schema import AiConfig

_MATCH_JSON = '{"confidence": 80}'


def _client(monkeypatch, **ai) -> tuple[OpenRouterClient, dict]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(model="parse/model", match_model="m", **ai))
    captured: dict = {}

    def fake_create(kwargs):
        captured.update(kwargs)
        msg = MagicMock(content=_MATCH_JSON, reasoning=None)
        resp = MagicMock(choices=[MagicMock(message=msg, finish_reason="stop")])
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
        return resp

    client._create_completion = fake_create  # type: ignore[method-assign]
    return client, captured


def test_effort_normalized_and_validated():
    assert AiConfig().match_reasoning_effort == ""
    assert AiConfig(match_reasoning_effort=None).match_reasoning_effort == ""
    assert AiConfig(match_reasoning_effort=" XHigh ").match_reasoning_effort == "xhigh"
    with pytest.raises(ValidationError):
        AiConfig(match_reasoning_effort="ultra")


def test_match_sends_effort(monkeypatch):
    client, captured = _client(monkeypatch, match_reasoning_enabled=True, match_reasoning_effort="high")
    client.match(resume_text="python", vacancy_text="нужен python")
    assert captured["extra_body"] == {"reasoning": {"effort": "high"}}
    assert captured["model"] == "m"


def test_match_without_effort_keeps_enabled(monkeypatch):
    client, captured = _client(monkeypatch, match_reasoning_enabled=True)
    client.match(resume_text="python", vacancy_text="нужен python")
    assert captured["extra_body"] == {"reasoning": {"enabled": True}}


def test_effort_ignored_when_reasoning_disabled(monkeypatch):
    client, captured = _client(monkeypatch, match_reasoning_effort="xhigh")
    client.match(resume_text="python", vacancy_text="нужен python")
    assert "extra_body" not in captured
    assert captured["response_format"]["type"] == "json_schema"


def test_cover_letter_shares_match_effort(monkeypatch):
    client, captured = _client(monkeypatch, match_reasoning_enabled=True, match_reasoning_effort="xhigh")
    client.cover_letter(resume_text="python", vacancy_text="нужен python")
    assert captured["extra_body"] == {"reasoning": {"effort": "xhigh"}}


def test_reasoning_exhausted_max_tokens_is_explicit_error(monkeypatch):
    client, _ = _client(monkeypatch, match_reasoning_enabled=True)

    def truncated(kwargs):
        msg = MagicMock(content="", reasoning="долго думаю...")
        resp = MagicMock(choices=[MagicMock(message=msg, finish_reason="length")])
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5000)
        return resp

    client._create_completion = truncated  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="max_tokens"):
        client.match(resume_text="python", vacancy_text="нужен python")
