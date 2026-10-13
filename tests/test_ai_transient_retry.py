"""Повторы LLM-вызова на транзиентных сбоях и статус прогона при отключении LLM в primary."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import tg_jobs_parser.parse_runner as parse_runner
from tg_jobs_parser.ai import rate_limit
from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient
from tg_jobs_parser.ai.rate_limit import LlmQuotaExhausted
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import (
    AiConfig,
    AppConfig,
    FxConfig,
    OutputConfig,
    ParserConfig,
    SourcesConfig,
)
from tg_jobs_parser.parsers.base import RawPost


class _ApiError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"upstream error {status_code}")
        self.status_code = status_code


class _FakeSdk:
    """``chat.completions.create``: отдаёт заготовленные исходы по очереди."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(rate_limit.time, "sleep", recorded.append)
    return recorded


def _client(monkeypatch, outcomes: list, **ai_overrides) -> tuple[OpenRouterClient, _FakeSdk]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(requests_per_minute=500, **ai_overrides))
    sdk = _FakeSdk(outcomes)
    monkeypatch.setattr(client, "_get_sdk", lambda: sdk)
    return client, sdk


def test_transient_errors_are_retried_with_backoff(monkeypatch, sleeps):
    client, sdk = _client(monkeypatch, [_ApiError(503), TimeoutError("read timed out"), "ok"])

    assert client._create_completion({}) == "ok"

    assert sdk.calls == 3
    backoff = [s for s in sleeps if s >= 2.0]
    assert len(backoff) == 2
    assert 2.0 <= backoff[0] <= 2.5 and 4.0 <= backoff[1] <= 5.0


def test_transient_retries_are_bounded(monkeypatch, sleeps):
    client, sdk = _client(monkeypatch, [_ApiError(502)] * 5, max_retries_on_error=2)

    with pytest.raises(RuntimeError, match="OpenRouter API"):
        client._create_completion({})

    assert sdk.calls == 3  # первая попытка + 2 повтора


def test_client_error_is_not_retried(monkeypatch, sleeps):
    client, sdk = _client(monkeypatch, [_ApiError(400), "never"])

    with pytest.raises(RuntimeError, match="OpenRouter API"):
        client._create_completion({})

    assert sdk.calls == 1


def test_retries_can_be_disabled(monkeypatch, sleeps):
    client, sdk = _client(monkeypatch, [_ApiError(503), "never"], max_retries_on_error=0)

    with pytest.raises(RuntimeError, match="OpenRouter API"):
        client._create_completion({})

    assert sdk.calls == 1


class _FakeChannelParser:
    def __init__(self, posts: list[RawPost]) -> None:
        self._posts = posts

    def fetch_posts_many(self, channels):
        return [(ch, list(self._posts), None) for ch in channels]


def _post(i: int) -> RawPost:
    return RawPost(
        channel_username="chan",
        post_id=str(i),
        text=f"Вакансия #{i}: Python backend developer, remote, зарплата от {300 + i}k, компания Acme{i}.",
        post_date=datetime.now(UTC),
        post_url=f"https://t.me/chan/{i}",
    )


def _run_with_quota_exhausted_after(monkeypatch, *, mode: str, ok_calls: int, posts: int):
    """run_parse, где LLM отвечает на первые ``ok_calls`` постов, а дальше исчерпывает квоту."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = AppConfig(
        sources=SourcesConfig(channels=["chan"]),
        parser=ParserConfig(
            vacancy_score_threshold=1,
            vacancy_min_substantive_score=1,
            vacancy_fuzzy_duplicate_threshold=101,
        ),
        output=OutputConfig(save_progress_batches=False),
        fx=FxConfig(enabled=False),
        ai=AiConfig(enabled=True, mode=mode, use_cache=False, concurrency=1, requests_per_minute=500),
    )
    calls = 0

    def fake_extract(self, *, text, search_context):
        nonlocal calls
        calls += 1
        if calls > ok_calls:
            self.rate_limiter.disable_llm_for_run("дневной лимит")
            raise LlmQuotaExhausted("дневной лимит")
        return VacancyAIExtraction(is_job_post=True, detected_specializations=["Python"], confidence=0.9), LLMUsage()

    monkeypatch.setattr(OpenRouterClient, "extract_with_fallback", fake_extract)
    monkeypatch.setattr(
        parse_runner,
        "_channel_parser",
        lambda config, cancellation=None: _FakeChannelParser([_post(i) for i in range(posts)]),
    )
    return parse_runner.run_parse(config)


def test_primary_mode_marks_run_incomplete_when_llm_disabled(monkeypatch):
    result = _run_with_quota_exhausted_after(monkeypatch, mode="primary", ok_calls=2, posts=5)

    assert len(result.vacancies_unique) == 2  # разобранное до отключения сохраняется
    assert result.incomplete_reason is not None
    assert "не разобрано постов: 3" in result.incomplete_reason
    assert "дневной лимит" in result.incomplete_reason


def test_assist_mode_falls_back_to_rules_and_stays_complete(monkeypatch):
    result = _run_with_quota_exhausted_after(monkeypatch, mode="assist", ok_calls=2, posts=5)

    assert len(result.vacancies_unique) == 5  # остаток разобран rules
    assert result.incomplete_reason is None
    assert "только rules" in (result.parse_warnings or "")
