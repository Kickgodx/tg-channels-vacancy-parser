"""Тесты цепочки fallback_models."""

from tg_jobs_parser.ai.llm_models_registry import apply_ai_defaults_from_registry, load_llm_models_registry
from tg_jobs_parser.config.schema import AiConfig


def test_resolved_fallback_models_dedupes_primary():
    reg = load_llm_models_registry("config.yaml")
    primary = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    ai_data = apply_ai_defaults_from_registry(
        {
            "model": primary,
            "fallback_models": [
                primary,
                "poolside/laguna-m.1:free",
                "poolside/laguna-m.1:free",
                "meta-llama/llama-3.3-70b-instruct:free",
            ],
        },
        reg,
    )
    cfg = AiConfig.model_validate(ai_data)
    assert cfg.resolved_fallback_models() == [
        "poolside/laguna-m.1:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ]


def test_migrate_legacy_fallback_model_key():
    cfg = AiConfig.model_validate(
        {
            "model": "deepseek/deepseek-v4-flash:free",
            "fallback_model": "poolside/laguna-m.1:free",
        }
    )
    assert cfg.fallback_models == ["poolside/laguna-m.1:free"]
