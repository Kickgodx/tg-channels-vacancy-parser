"""Тесты цепочки fallback_models."""

from tg_jobs_parser.ai.llm_models_registry import (
    apply_ai_defaults_from_registry,
    load_llm_models_registry,
)
from tg_jobs_parser.config.schema import AiConfig


def test_resolved_fallback_models_dedupes_primary():
    reg = load_llm_models_registry("config.yaml")
    primary = "deepseek/deepseek-v4.1-flash"
    secondary = "xiaomi/mimo-v2.6-flash"
    ai_data = apply_ai_defaults_from_registry(
        {
            "model": primary,
            "fallback_models": [
                primary,
                "google/gemini-3.1-flash-lite",
                "google/gemini-3.1-flash-lite",
                secondary,
            ],
        },
        reg,
    )
    cfg = AiConfig.model_validate(ai_data)
    assert cfg.resolved_fallback_models() == [
        "google/gemini-3.1-flash-lite",
        secondary,
    ]


def test_migrate_legacy_fallback_model_key():
    cfg = AiConfig.model_validate(
        {
            "model": "deepseek/deepseek-v4.1-flash",
            "fallback_model": "google/gemini-3.1-flash-lite",
        }
    )
    assert cfg.fallback_models == ["google/gemini-3.1-flash-lite"]
