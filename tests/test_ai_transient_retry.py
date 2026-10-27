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
            fuzzy_text_duplicate_threshold=101,
        ),
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


def _disabled(reason: str) -> float:
    from prometheus_client import REGISTRY

    value = REGISTRY.get_sample_value("llm_enrich_disabled_total", {"reason": reason})
    assert value is not None  # серия заведена нулём до первого события
    return value


def test_quota_exhausted_counts_llm_disabled_once(monkeypatch):
    before = _disabled("quota")
    _run_with_quota_exhausted_after(monkeypatch, mode="assist", ok_calls=1, posts=6)
    # Квота кончилась на 2-м посте, ещё 4 прошли без LLM — событие одно.
    assert _disabled("quota") == before + 1


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


def _run_with_extract(monkeypatch, fake_extract, *, mode: str, posts: int, **ai_overrides):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    config = AppConfig(
        sources=SourcesConfig(channels=["chan"]),
        parser=ParserConfig(
            vacancy_score_threshold=1,
            vacancy_min_substantive_score=1,
            vacancy_fuzzy_duplicate_threshold=101,
            fuzzy_text_duplicate_threshold=101,
        ),
        fx=FxConfig(enabled=False),
        ai=AiConfig(enabled=True, mode=mode, use_cache=False, concurrency=1, requests_per_minute=500, **ai_overrides),
    )
    monkeypatch.setattr(OpenRouterClient, "extract_with_fallback", fake_extract)
    monkeypatch.setattr(
        parse_runner,
        "_channel_parser",
        lambda config, cancellation=None: _FakeChannelParser([_post(i) for i in range(posts)]),
    )
    return parse_runner.run_parse(config)


_OK = VacancyAIExtraction(is_job_post=True, detected_specializations=["Python"], confidence=0.9)


def test_circuit_breaker_stops_llm_after_consecutive_errors(monkeypatch):
    calls = 0

    def always_fails(self, *, text, search_context):
        nonlocal calls
        calls += 1
        raise RuntimeError("OpenRouter API: upstream error 503")

    breaker_before, quota_before = _disabled("circuit_breaker"), _disabled("quota")
    result = _run_with_extract(monkeypatch, always_fails, mode="primary", posts=10, circuit_breaker_errors=3)

    assert calls == 3  # остальные 7 постов не ждут повторов и таймаутов
    assert _disabled("circuit_breaker") == breaker_before + 1
    assert _disabled("quota") == quota_before
    assert result.vacancies_unique == []
    assert result.incomplete_reason is not None
    assert "circuit breaker" in result.incomplete_reason
    assert "не разобрано постов: 7" in result.incomplete_reason


def test_circuit_breaker_resets_on_success(monkeypatch):
    calls = 0

    def every_other_fails(self, *, text, search_context):
        nonlocal calls
        calls += 1
        if calls % 2:
            raise RuntimeError("OpenRouter API: upstream error 503")
        return _OK, LLMUsage()

    result = _run_with_extract(
        monkeypatch, every_other_fails, mode="assist", posts=8, circuit_breaker_errors=2, max_error_ratio=1
    )

    assert calls == 8  # ошибок подряд не больше одной — breaker не срабатывает
    assert result.incomplete_reason is None
    assert "LLM отключён" not in (result.parse_warnings or "")


def test_circuit_breaker_in_assist_mode_falls_back_to_rules(monkeypatch):
    def always_fails(self, *, text, search_context):
        raise RuntimeError("OpenRouter API: upstream error 503")

    result = _run_with_extract(monkeypatch, always_fails, mode="assist", posts=6, circuit_breaker_errors=2)

    assert len(result.vacancies_unique) == 6
    assert result.incomplete_reason is None
    assert "circuit breaker" in (result.parse_warnings or "")


def _fails_on(call_numbers: set[int]):
    calls = 0

    def extract(self, *, text, search_context):
        nonlocal calls
        calls += 1
        if calls in call_numbers:
            raise RuntimeError("OpenRouter API: bad JSON")
        return _OK, LLMUsage()

    return extract


def test_primary_error_ratio_above_threshold_marks_run_incomplete(monkeypatch):
    result = _run_with_extract(monkeypatch, _fails_on({2, 5}), mode="primary", posts=10, max_error_ratio=0.1)

    assert len(result.vacancies_unique) == 8
    assert result.incomplete_reason is not None
    assert "ошибок LLM 2 из 10" in result.incomplete_reason


def test_primary_error_ratio_within_threshold_stays_complete(monkeypatch):
    result = _run_with_extract(monkeypatch, _fails_on({2, 5}), mode="primary", posts=10, max_error_ratio=0.2)

    assert len(result.vacancies_unique) == 8
    assert result.incomplete_reason is None


class _StatusError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_rate_limit_is_detected_by_status_code_not_text():
    from tg_jobs_parser.ai.clients.openrouter import _is_rate_limit_error

    assert _is_rate_limit_error(_StatusError(429, "Too Many Requests")) is True
    # Клиент оборачивает ошибку SDK: статус ищется по цепочке __cause__.
    try:
        try:
            raise _StatusError(429, "slow down")
        except _StatusError as inner:
            raise RuntimeError(f"OpenRouter API: {inner}") from inner
    except RuntimeError as wrapped:
        assert _is_rate_limit_error(wrapped) is True
    # «429» в тексте (id запроса, ответ модели) — не rate limit.
    assert _is_rate_limit_error(_StatusError(500, "request req-4291 failed")) is False
    assert _is_rate_limit_error(RuntimeError("OpenRouter API: model said 429")) is False


def test_text_with_429_still_gets_fallback_model(monkeypatch, sleeps):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(requests_per_minute=500, fallback_models=["fb/model"]))
    used: list[str | None] = []

    def fake_extract(*, text, search_context, model=None):
        used.append(model)
        if model is None:
            raise RuntimeError("OpenRouter API: invalid JSON near offset 429")
        return _OK, LLMUsage(model=model)

    monkeypatch.setattr(client, "extract", fake_extract)

    _, usage = client.extract_with_fallback(text="t", search_context=None)

    assert used == [None, "fb/model"]
    assert usage.model == "fb/model"
