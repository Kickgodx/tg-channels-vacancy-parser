"""Наблюдаемость прогона: service.name воркеров, ожидание RPM-лимита и стоимость LLM,
span'ы каналов Telegram, FloodWait, поток обработанных постов."""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from prometheus_client import REGISTRY
from telethon.client.users import _fmt_flood
from telethon.errors import FloodWaitError
from tests.test_incremental_db_flush import _post
from tests.test_incremental_db_flush import _run as run_parse_with_posts
from tests.test_telethon_incremental import MESSAGES, PAGED_MESSAGES, FakeClient, _parser

from backend.tracing import DEFAULT_SERVICE_NAME, service_name
from tg_jobs_parser.ai.models_catalog import LlmModelEntry
from tg_jobs_parser.config.schema import AiConfig
from tg_jobs_parser.observability.parse_metrics import install_telethon_flood_filter


@pytest.fixture
def exporter(otel_exporter):
    return otel_exporter


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == name and sample.labels == (labels or {}):
                return float(sample.value)
    return 0.0


def test_service_name_follows_otel_env(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "tg-jobs-parser-parse-worker")
    assert service_name() == "tg-jobs-parser-parse-worker"
    monkeypatch.setenv("OTEL_SERVICE_NAME", " ")
    assert service_name() == DEFAULT_SERVICE_NAME
    monkeypatch.delenv("OTEL_SERVICE_NAME")
    assert service_name() == DEFAULT_SERVICE_NAME


def _client_with_sdk(monkeypatch, *, model: str, catalog, acquire_sleep: float = 0.0, errors=()):
    from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(model=model, requests_per_minute=500), catalog=catalog)
    pending_errors = list(errors)

    def create(**kwargs):
        if pending_errors:
            raise pending_errors.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"is_job_post": true, "confidence": 0.9}'),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=200_000),
        )

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(client, "_get_sdk", lambda: sdk)
    original_acquire = client.rate_limiter.acquire

    def slow_acquire():
        time.sleep(acquire_sleep)
        original_acquire()

    monkeypatch.setattr(client.rate_limiter, "acquire", slow_acquire)
    monkeypatch.setattr(client.rate_limiter, "wait_after_transient_error", lambda attempt, err: None)
    return client


def test_llm_span_has_rate_gate_wait_retries_and_cost(exporter, monkeypatch):
    from tests.test_llm_metrics import _search_context

    model = "obs/priced-model"
    client = _client_with_sdk(
        monkeypatch,
        model=model,
        catalog=[LlmModelEntry(id=model, name="Priced", cost="0.10/0.50")],
        acquire_sleep=0.05,
        errors=[TimeoutError("slow upstream")],
    )
    gate_before = _sample("llm_rate_gate_wait_seconds_count")
    cost_before = _sample("llm_cost_usd_total", {"model": model})
    retry_before = _sample("llm_retries_total", {"model": model, "reason": "transient"})

    with trace.get_tracer("test").start_as_current_span("parse.process_posts"):
        client.extract(text="Python developer", search_context=_search_context())

    span = next(s for s in exporter.get_finished_spans() if s.name == f"chat {model}")
    # Два захода в лимит (исходный запрос + повтор после таймаута), каждый ≥ 50 мс.
    assert span.attributes["llm.rate_gate.wait_seconds"] >= 0.1
    assert span.attributes["llm.retries"] == 1
    # 1M input × $0.10 + 0.2M output × $0.50 = $0.20
    assert span.attributes["llm.cost_usd"] == pytest.approx(0.2)
    assert _sample("llm_rate_gate_wait_seconds_count") == gate_before + 2
    assert _sample("llm_cost_usd_total", {"model": model}) == pytest.approx(cost_before + 0.2)
    assert _sample("llm_retries_total", {"model": model, "reason": "transient"}) == retry_before + 1


def test_llm_without_catalog_price_records_no_cost(exporter, monkeypatch):
    from tests.test_llm_metrics import _search_context

    model = "obs/unpriced-model"
    client = _client_with_sdk(monkeypatch, model=model, catalog=None)
    with trace.get_tracer("test").start_as_current_span("parse.process_posts"):
        client.extract(text="Python developer", search_context=_search_context())

    span = next(s for s in exporter.get_finished_spans() if s.name == f"chat {model}")
    assert "llm.cost_usd" not in span.attributes
    assert _sample("llm_cost_usd_total", {"model": model}) == 0.0


def test_telegram_channel_span_and_metrics(exporter):
    ok_before = _sample("telegram_channels_fetched_total", {"result": "ok"})
    posts_before = _sample("telegram_posts_fetched_total")

    parser = _parser()
    with trace.get_tracer("test").start_as_current_span("parse.fetch_posts"):
        posts, warn = asyncio.run(parser._iter_channel(FakeClient(MESSAGES), "chan"))

    assert warn is None
    spans = {s.name: s for s in exporter.get_finished_spans()}
    channel = spans["telegram.channel"]
    assert channel.parent.span_id == spans["parse.fetch_posts"].context.span_id
    assert channel.attributes["telegram.channel"] == "chan"
    assert channel.attributes["telegram.result"] == "ok"
    assert channel.attributes["telegram.posts"] == len(posts)
    assert channel.attributes["telegram.messages_scanned"] >= len(posts)
    assert _sample("telegram_channels_fetched_total", {"result": "ok"}) == ok_before + 1
    assert _sample("telegram_posts_fetched_total") == posts_before + len(posts)


def test_telegram_page_pauses_are_measured(exporter):
    paused_before = _sample("telegram_request_pause_seconds_total")

    parser = _parser(sleep_between_requests_seconds=0.01)
    asyncio.run(parser._iter_channel(FakeClient(PAGED_MESSAGES), "chan"))

    channel = next(s for s in exporter.get_finished_spans() if s.name == "telegram.channel")
    paused = _sample("telegram_request_pause_seconds_total") - paused_before
    assert paused >= 0.02  # две паузы по 10 мс
    assert channel.attributes["telegram.pause_seconds"] == pytest.approx(paused, abs=0.001)


def test_flood_wait_gave_up_marks_channel_partial(exporter):
    partial_before = _sample("telegram_channels_fetched_total", {"result": "partial"})
    gave_up_before = _sample("telegram_flood_waits_total", {"handled_by": "gave_up"})

    parser = _parser(flood_wait_max_seconds=60)
    client = FakeClient(MESSAGES, fail_after=2, error=FloodWaitError(request=None, capture=3600))
    asyncio.run(parser._iter_channel(client, "chan"))

    channel = next(s for s in exporter.get_finished_spans() if s.name == "telegram.channel")
    assert channel.attributes["telegram.result"] == "partial"
    event = next(e for e in channel.events if e.name == "telegram.flood_wait")
    assert event.attributes["handled_by"] == "gave_up"
    assert event.attributes["telegram.flood_wait.seconds"] == 3600
    assert _sample("telegram_channels_fetched_total", {"result": "partial"}) == partial_before + 1
    assert _sample("telegram_flood_waits_total", {"handled_by": "gave_up"}) == gave_up_before + 1


@pytest.mark.parametrize("early", [False, True])
def test_telethon_auto_flood_sleep_is_counted(exporter, caplog, early):
    """Короткий FloodWait Telethon пережидает сам — видно только по его INFO-логу."""
    caplog.set_level(logging.INFO, logger="telethon.client.users")
    install_telethon_flood_filter()
    install_telethon_flood_filter()  # идемпотентно: второй вызов не удваивает счёт
    waits_before = _sample("telegram_flood_waits_total", {"handled_by": "telethon"})
    seconds_before = _sample("telegram_flood_wait_seconds_total", {"handled_by": "telethon"})

    class GetHistoryRequest:
        pass

    logger = logging.getLogger("telethon.client.users")
    with trace.get_tracer("test").start_as_current_span("telegram.channel"):
        logger.info(*_fmt_flood(25, GetHistoryRequest(), early=early))

    assert _sample("telegram_flood_waits_total", {"handled_by": "telethon"}) == waits_before + 1
    assert _sample("telegram_flood_wait_seconds_total", {"handled_by": "telethon"}) == seconds_before + 25
    event = exporter.get_finished_spans()[0].events[0]
    assert event.attributes["telegram.request"] == "GetHistoryRequest"
    assert event.attributes["telegram.flood_wait.seconds"] == 25


def test_run_parse_counts_processed_posts_and_flush_spans(exporter, monkeypatch):
    candidate_before = _sample("parse_posts_processed_total", {"result": "candidate"})
    dropped_before = _sample("parse_posts_processed_total", {"result": "dropped"})

    posts = [_post(1), _post(2), _post(3, text="привет")]
    run_parse_with_posts(monkeypatch, posts, sink=lambda batch: None)

    candidate = _sample("parse_posts_processed_total", {"result": "candidate"}) - candidate_before
    dropped = _sample("parse_posts_processed_total", {"result": "dropped"}) - dropped_before
    assert candidate + dropped == len(posts)
    assert candidate >= 1 and dropped >= 1
    flushes = [s for s in exporter.get_finished_spans() if s.name == "parse.flush_vacancies"]
    assert flushes and all(s.attributes["parse.vacancies"] >= 1 for s in flushes)


def _has_series(name: str, labels: dict[str, str]) -> bool:
    return any(
        sample.name == name and sample.labels == labels for family in REGISTRY.collect() for sample in family.samples
    )


@pytest.mark.parametrize(
    ("name", "labels"),
    [
        ("telegram_channels_fetched_total", {"result": "partial"}),
        ("telegram_flood_waits_total", {"handled_by": "gave_up"}),
        ("telegram_flood_wait_seconds_total", {"handled_by": "resumed"}),
        ("parse_posts_processed_total", {"result": "candidate"}),
        ("parse_phase_duration_seconds_count", {"phase": "dedup"}),
        ("llm_enrich_events_total", {"event": "circuit_open"}),
        ("parse_runs_total", {"status": "partial"}),
        ("vacancies_upserted_total", {"result": "updated"}),
    ],
)
def test_labeled_series_exported_before_first_event(name, labels):
    """Серия, рождённая сразу ненулевой, теряет первый прирост в increase()/rate() —
    короткий прогон на свежем воркере не виден на дашборде совсем."""
    import backend.observability  # noqa: F401 — регистрирует parse_runs/vacancies_upserted

    assert _has_series(name, labels)


def test_model_series_exported_when_client_created(monkeypatch):
    from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    OpenRouterClient(AiConfig(model="obs/fresh-model", match_model="obs/fresh-match"))

    for model in ("obs/fresh-model", "obs/fresh-match"):
        assert _sample("llm_requests_total", {"model": model, "outcome": "success"}) == 0.0
        assert _has_series("llm_requests_total", {"model": model, "outcome": "success"})
        assert _has_series("llm_tokens_total", {"model": model, "direction": "input"})
        assert _has_series("llm_cost_usd_total", {"model": model})
        assert _has_series("llm_retries_total", {"model": model, "reason": "rate_limit"})
        assert _has_series("llm_request_duration_seconds_count", {"model": model})


def test_ai_config_resolved_models_dedupes():
    ai = AiConfig(
        model="a",
        fallback_models=["b", "a", " "],
        match_model="b",
        cover_letter_model="c",
    )
    assert ai.resolved_models() == ["a", "b", "c"]
