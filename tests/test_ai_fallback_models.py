"""Тесты цепочки fallback_models."""

from tg_jobs_parser.config.schema import AiConfig


def test_resolved_fallback_models_dedupes_primary():
    cfg = AiConfig(
        model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        fallback_models=[
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "poolside/laguna-m.1:free",
            "poolside/laguna-m.1:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
    )
    assert cfg.resolved_fallback_models() == [
        "poolside/laguna-m.1:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ]


def test_migrate_legacy_fallback_model_key():
    cfg = AiConfig.model_validate(
        {
            "model": "a:free",
            "fallback_model": "b:free",
        }
    )
    assert cfg.resolved_fallback_models() == ["b:free"]
