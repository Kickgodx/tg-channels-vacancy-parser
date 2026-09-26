"""Offline evals: scorer + golden fixtures (без сети)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tg_jobs_parser.ai.prompts import (
    EXTRACT_PROMPT_VERSION,
    MATCH_PROMPT_VERSION,
    COVER_LETTER_PROMPT_VERSION,
    resolve_extract_prompt_version,
)
from tg_jobs_parser.evals.golden import load_extraction_cases
from tg_jobs_parser.evals.paths import fixtures_extraction_path, golden_extraction_path
from tg_jobs_parser.evals.score_extraction import aggregate_scores, score_extraction

# Мягкий порог на старте (план): fixtures ≈ expected → высокий score.
_MIN_EXACT_MATCH_RATE = 0.8


def test_prompt_versions_defined():
    assert EXTRACT_PROMPT_VERSION.startswith("extract-")
    assert MATCH_PROMPT_VERSION.startswith("match-")
    assert COVER_LETTER_PROMPT_VERSION.startswith("cover-letter-")


def test_resolve_extract_prompt_version_warns_on_mismatch():
    with pytest.warns(UserWarning, match="EXTRACT_PROMPT_VERSION"):
        assert resolve_extract_prompt_version("v1") == EXTRACT_PROMPT_VERSION
    assert resolve_extract_prompt_version(EXTRACT_PROMPT_VERSION) == EXTRACT_PROMPT_VERSION
    assert resolve_extract_prompt_version(None) == EXTRACT_PROMPT_VERSION


def test_score_extraction_exact_match():
    expected = {
        "is_job_post": True,
        "detected_specializations": ["Go"],
        "salary_min": 100000,
        "salary_max": 200000,
    }
    actual = {
        "is_job_post": True,
        "detected_specializations": ["go"],
        "salary_min": 100000,
        "salary_max": 200000,
        "notes": "extra ignored",
    }
    s = score_extraction(actual, expected)
    assert s["all_matched"] is True
    assert s["accuracy"] == 1.0


def test_score_extraction_detects_mismatch():
    expected = {"is_job_post": True, "detected_specializations": ["Go"]}
    actual = {"is_job_post": True, "detected_specializations": ["Go", "QA"]}
    s = score_extraction(actual, expected)
    assert s["all_matched"] is False
    assert s["field_results"]["detected_specializations"] is False


def test_score_salary_tolerance():
    expected = {"salary_min": 200000}
    assert score_extraction({"salary_min": 200001}, expected)["all_matched"] is True
    assert score_extraction({"salary_min": 250000}, expected)["all_matched"] is False


def test_aggregate_scores():
    scores = [
        score_extraction({"is_job_post": True}, {"is_job_post": True}),
        score_extraction({"is_job_post": False}, {"is_job_post": True}),
    ]
    agg = aggregate_scores(scores)
    assert agg["cases"] == 2
    assert agg["exact_match_rate"] == 0.5
    assert agg["per_field"]["is_job_post"] == 0.5


def test_golden_dataset_loads():
    cases = load_extraction_cases()
    assert len(cases) >= 15
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    assert golden_extraction_path().is_file()


def test_golden_offline_fixtures_meet_threshold():
    """Fixtures = сохранённые «хорошие» ответы; проверяем pipeline без LLM."""
    cases = load_extraction_cases()
    fixtures_path = fixtures_extraction_path()
    assert fixtures_path.is_file()
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    scores = []
    missing = []
    for case in cases:
        cid = case["id"]
        if cid not in fixtures:
            missing.append(cid)
            continue
        scores.append(score_extraction(fixtures[cid], case["expected"]))
    assert not missing, f"нет fixtures для: {missing}"
    agg = aggregate_scores(scores)
    assert agg["exact_match_rate"] >= _MIN_EXACT_MATCH_RATE, agg
    assert agg["mean_field_accuracy"] >= _MIN_EXACT_MATCH_RATE, agg


@pytest.mark.llm
def test_live_extraction_smoke(monkeypatch):
    """Один кейс через OpenRouter — только с -m llm и ключом."""
    import os

    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        pytest.skip("OPENROUTER_API_KEY не задан")
    config_path = Path("config.yaml")
    if not config_path.is_file():
        pytest.skip("config.yaml не найден")

    from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient
    from tg_jobs_parser.config.loader import load_config
    from tg_jobs_parser.evals.run_extraction import _default_search_context

    cfg = load_config(config_path)
    if not cfg.ai.enabled:
        pytest.skip("ai.enabled=false")

    cases = load_extraction_cases()
    case = next(c for c in cases if c["id"] == "resume-go-ru")
    client = OpenRouterClient(cfg.ai)
    try:
        extraction, _usage = client.extract_with_fallback(
            text=case["text"],
            search_context=_default_search_context(),
        )
    finally:
        client.close()
    s = score_extraction(extraction, case["expected"])
    assert s["field_results"].get("is_job_post") is True
