"""Тесты Prometheus-метрик LLM."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from prometheus_client import REGISTRY

from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient
from tg_jobs_parser.ai.enricher import AIEnrichmentService
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.vacancy import Vacancy


def _counter(name: str, labels: dict[str, str]) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == name and sample.labels == labels:
                return float(sample.value)
    return 0.0


def _vacancy() -> Vacancy:
    return Vacancy(
        id="ch-1",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="Senior Go dev remote 400k",
        cleaned_text="Senior Go dev remote 400k",
        vacancy_score=4,
        parsed_at=datetime.now(timezone.utc),
    )


def _search_context() -> SearchContext:
    return SearchContext(
        target_specializations=["Python"],
        target_work_formats=["any"],
        exclude_keywords=[],
        allowed_specializations=["Python"],
        allowed_work_formats=["any"],
    )


def test_llm_enrich_metrics_skipped_score(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "ai": cfg.ai.model_copy(
                update={"enabled": True, "skip_if_vacancy_score_below": 3, "use_cache": False}
            ),
        }
    )
    service = AIEnrichmentService(cfg)
    before = _counter("llm_enrich_events_total", {"event": "skipped_score"})

    service.begin_batch(1)
    assert service.enrich(_vacancy().model_copy(update={"vacancy_score": 1})) is None

    assert _counter("llm_enrich_events_total", {"event": "skipped_score"}) == before + 1


def test_llm_openrouter_metrics_on_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"model": "test/model"})})
    model = "test/model"
    client = OpenRouterClient(cfg.ai)

    response = MagicMock()
    response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"is_job_post": true, "confidence": 0.9, "detected_specializations": ["Go"]}'
            )
        )
    ]
    response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

    before_req = _counter("llm_requests_total", {"model": model, "outcome": "success"})
    before_in = _counter("llm_tokens_total", {"model": model, "direction": "input"})

    with patch.object(client, "_create_completion", return_value=response):
        extraction, usage = client.extract(text="vacancy text", search_context=_search_context())

    assert extraction.is_job_post is True
    assert usage.prompt_tokens == 100
    assert _counter("llm_requests_total", {"model": model, "outcome": "success"}) == before_req + 1
    assert _counter("llm_tokens_total", {"model": model, "direction": "input"}) == before_in + 100


def test_llm_enrich_metrics_cache_hit(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "use_cache": True})})
    service = AIEnrichmentService(cfg)
    cached = VacancyAIExtraction(is_job_post=True, confidence=0.9)

    before = _counter("llm_enrich_events_total", {"event": "cache_hit"})
    before_merged = _counter("llm_enrich_events_total", {"event": "merged"})
    service.begin_batch(1)
    with patch.object(service._cache, "get", return_value=cached):
        assert service.enrich(_vacancy()) is not None

    assert _counter("llm_enrich_events_total", {"event": "cache_hit"}) == before + 1
    assert _counter("llm_enrich_events_total", {"event": "merged"}) == before_merged + 1


def test_llm_enrich_metrics_merged_on_llm_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={"ai": cfg.ai.model_copy(update={"enabled": True, "use_cache": False})}
    )
    service = AIEnrichmentService(cfg)
    fake = VacancyAIExtraction(is_job_post=True, confidence=0.9)
    before = _counter("llm_enrich_events_total", {"event": "merged"})
    service.begin_batch(1)
    with patch.object(
        service._client, "extract_with_fallback", return_value=(fake, LLMUsage())
    ):
        assert service.enrich(_vacancy()) is not None
    assert _counter("llm_enrich_events_total", {"event": "merged"}) == before + 1
