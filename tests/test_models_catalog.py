"""Каталог моделей OpenRouter для UI."""

from tg_jobs_parser.ai.models_catalog import (
    OPENROUTER_FREE_MODELS,
    merge_openrouter_model_options,
)


def test_merge_puts_config_models_first():
    primary = "google/gemini-3.1-flash-lite"
    fallbacks = ["deepseek/deepseek-v4-flash:free", "poolside/laguna-m.1:free"]
    opts = merge_openrouter_model_options(primary, fallbacks)
    assert opts[0] == primary
    assert opts[1:3] == fallbacks
    assert "poolside/laguna-m.1:free" in opts


def test_merge_dedupes_catalog_overlap():
    primary = OPENROUTER_FREE_MODELS[1]
    opts = merge_openrouter_model_options(primary, [OPENROUTER_FREE_MODELS[0]])
    assert opts.count(primary) == 1
    assert opts[0] == primary
