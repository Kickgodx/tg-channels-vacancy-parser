"""Мок OpenRouter для enricher."""

from datetime import UTC, datetime
from unittest.mock import patch

from tg_jobs_parser.ai.clients.openrouter import LLMUsage
from tg_jobs_parser.ai.enricher import AIEnrichmentService
from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.vacancy import Vacancy


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
        detected_specializations=["Go"],
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
    assert out.detected_specializations == ["Go"]
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
