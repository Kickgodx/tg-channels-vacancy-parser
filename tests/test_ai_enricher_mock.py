"""Мок OpenRouter для enricher."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from jobdesk.ai.clients.openrouter import LLMUsage
from jobdesk.ai.enricher import AIEnrichmentService
from jobdesk.ai.schema import VacancyAIExtraction
from jobdesk.config.schema import AppConfig
from jobdesk.models.vacancy import Vacancy


def _vacancy() -> Vacancy:
    return Vacancy(
        id="ch-1",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="Senior Go dev remote 400k https://example.com/job",
        cleaned_text="Senior Go dev remote 400k https://example.com/job",
        vacancy_score=4,
        parsed_at=datetime.now(UTC),
    )


def test_enrich_skips_low_vacancy_score(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "ai": cfg.ai.model_copy(
                update={
                    "enabled": True,
                    "skip_if_vacancy_score_below": 3,
                    "use_cache": False,
                }
            ),
        }
    )
    service = AIEnrichmentService(cfg)
    low = _vacancy().model_copy(update={"vacancy_score": 1})
    service.begin_batch(1)
    assert service.enrich(low) is None
    assert service.stats.skipped_score == 1


def test_enrich_drops_non_job(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "use_cache": False})})
    service = AIEnrichmentService(cfg)
    fake = VacancyAIExtraction(is_job_post=False, rejection_reason="resume", confidence=0.9)

    service.begin_batch(10)
    with patch.object(service._client, "extract_with_fallback", return_value=(fake, LLMUsage())):
        assert service.enrich(_vacancy()) is None
        assert service.stats.dropped_not_job == 1
        assert service._progress_done == 1


def test_enrich_rules_only_after_quota(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "use_cache": False})})
    service = AIEnrichmentService(cfg)
    service._client.rate_limiter.disable_llm_for_run("test")
    out = service.enrich(_vacancy())
    assert out is not None
    assert out.extraction_source == "rules"


def test_enrich_merges_job(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "ai": cfg.ai.model_copy(update={"enabled": True, "use_cache": False}),
            "parser": cfg.parser.model_copy(update={"vacancy_score_threshold": 3}),
        }
    )
    service = AIEnrichmentService(cfg)
    fake = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["Backend", "Go"],
        salary_min=400000,
        salary_currency="RUB",
        work_format="remote",
        confidence=0.9,
    )
    service.begin_batch(5)
    with patch.object(service._client, "extract_with_fallback", return_value=(fake, LLMUsage())):
        out = service.enrich(_vacancy())
    assert out is not None
    assert service._progress_done == 1
    # Язык из ответа модели — в технологиях, а не в специализациях.
    assert out.detected_specializations == ["Backend"]
    assert "Go" in out.technologies
    assert out.vacancy_score >= 3


def test_enrich_caches_fallback_answer_under_fallback_model(monkeypatch):
    """Ответ fallback-модели не должен попасть в кэш под ключом основной."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True, "model": "primary/model"})})
    service = AIEnrichmentService(cfg)
    fake = VacancyAIExtraction(is_job_post=False, confidence=0.9)
    service.begin_batch(1)
    with (
        patch.object(service._cache, "get", return_value=None),
        patch.object(service._cache, "put") as put,
        patch.object(
            service._client,
            "extract_with_fallback",
            return_value=(fake, LLMUsage(model="fallback/model")),
        ),
    ):
        service.enrich(_vacancy())
    assert put.call_args.kwargs["model"] == "fallback/model"


class _MemCache:
    """Кэш LLM в памяти: ``hits`` — тексты с готовым ответом, ``puts`` — что записано."""

    def __init__(self, hits: dict[str, VacancyAIExtraction] | None = None) -> None:
        self.hits = dict(hits or {})
        self.puts: list[str] = []

    def get(self, *, text, model, prompt_version, mode):
        return self.hits.get(text)

    def put(self, *, text, model, prompt_version, mode, extraction):
        self.puts.append(text)


def _limited_service(monkeypatch, *, mode: str, limit: int, cache: _MemCache, concurrency: int = 1):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "ai": cfg.ai.model_copy(
                update={"enabled": True, "mode": mode, "max_posts_per_run": limit, "concurrency": concurrency}
            )
        }
    )
    service = AIEnrichmentService(cfg, cache=cache)
    service.begin_batch(10)
    return service


def _post(n: int) -> Vacancy:
    text = f"Senior Go dev #{n} remote 400k"
    return _vacancy().model_copy(update={"id": f"ch-{n}", "raw_text": text, "cleaned_text": text})


_JOB = VacancyAIExtraction(is_job_post=True, confidence=0.9, detected_grade="senior")


def test_primary_drops_post_over_max_posts_per_run(monkeypatch):
    cache = _MemCache()
    service = _limited_service(monkeypatch, mode="primary", limit=1, cache=cache)

    with patch.object(service._client, "extract_with_fallback", return_value=(_JOB, LLMUsage())) as call:
        first = service.enrich(_post(1))
        second = service.enrich(_post(2))

    assert first is not None and first.extraction_source == "ai"
    assert second is None
    assert call.call_count == 1
    assert cache.puts == [_post(1).cleaned_text]  # пропущенный пост не кэшируется
    assert (service.stats.calls, service.stats.skipped_limit) == (1, 1)


def test_assist_keeps_rules_vacancy_over_max_posts_per_run(monkeypatch):
    service = _limited_service(monkeypatch, mode="assist", limit=1, cache=_MemCache())

    with patch.object(service._client, "extract_with_fallback", return_value=(_JOB, LLMUsage())):
        service.enrich(_post(1))
        second = service.enrich(_post(2))

    assert second is not None
    assert second.extraction_source == "rules"
    assert service.stats.skipped_limit == 1


@pytest.mark.parametrize(("mode", "source"), [("primary", "ai"), ("assist", "hybrid")])
def test_cached_answer_is_used_after_max_posts_per_run(monkeypatch, mode, source):
    cache = _MemCache({_post(2).cleaned_text: _JOB})
    service = _limited_service(monkeypatch, mode=mode, limit=1, cache=cache)

    with patch.object(service._client, "extract_with_fallback", return_value=(_JOB, LLMUsage())) as call:
        service.enrich(_post(1))
        cached = service.enrich(_post(2))

    assert cached is not None and cached.extraction_source == source
    assert call.call_count == 1
    assert (service.stats.cache_hits, service.stats.skipped_limit) == (1, 0)


def test_max_posts_per_run_holds_under_concurrency(monkeypatch):
    service = _limited_service(monkeypatch, mode="primary", limit=3, cache=_MemCache(), concurrency=8)
    calls: list[str] = []
    lock = threading.Lock()

    def slow_extract(*, text, search_context):
        with lock:
            calls.append(text)
        time.sleep(0.02)
        return _JOB, LLMUsage()

    with (
        patch.object(service._client, "extract_with_fallback", side_effect=slow_extract),
        ThreadPoolExecutor(max_workers=8) as pool,
    ):
        results = list(pool.map(service.enrich, [_post(n) for n in range(10)]))

    assert len(calls) == 3
    assert sum(r is not None for r in results) == 3
    assert (service.stats.calls, service.stats.skipped_limit) == (3, 7)


def _accepted_by_llm(monkeypatch, vacancy: Vacancy, *, exclude: list[str]) -> tuple[Vacancy | None, AppConfig]:
    """Ответ LLM «вакансия» при пороге содержательной оценки 1."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = AppConfig()
    cfg = cfg.model_copy(
        update={
            "ai": cfg.ai.model_copy(update={"enabled": True, "use_cache": False}),
            "parser": cfg.parser.model_copy(update={"vacancy_min_substantive_score": 1}),
            "filters": cfg.filters.model_copy(update={"exclude_keywords": exclude}),
        }
    )
    service = AIEnrichmentService(cfg)
    fake = VacancyAIExtraction(is_job_post=True, confidence=0.9)
    service.begin_batch(1)
    with patch.object(service._client, "extract_with_fallback", return_value=(fake, LLMUsage())):
        return service.enrich(vacancy), cfg


def test_llm_verdict_lifts_substantive_score_to_threshold(monkeypatch):
    from jobdesk.filters.vacancy_filters import apply_post_filters

    only_contact = _vacancy().model_copy(update={"vacancy_score": 1, "vacancy_substantive_score": 0})

    enriched, cfg = _accepted_by_llm(monkeypatch, only_contact, exclude=[])

    assert enriched is not None
    assert enriched.vacancy_substantive_score >= 1
    assert enriched.vacancy_score >= cfg.parser.vacancy_score_threshold
    assert apply_post_filters(enriched, cfg) is True


def test_llm_verdict_keeps_post_filters(monkeypatch):
    from jobdesk.filters.vacancy_filters import apply_post_filters

    casino = _vacancy().model_copy(
        update={
            "raw_text": "Go dev в онлайн-казино",
            "cleaned_text": "Go dev в онлайн-казино",
            "vacancy_substantive_score": 0,
        }
    )

    enriched, cfg = _accepted_by_llm(monkeypatch, casino, exclude=["казино"])

    assert enriched is not None
    assert apply_post_filters(enriched, cfg) is False
