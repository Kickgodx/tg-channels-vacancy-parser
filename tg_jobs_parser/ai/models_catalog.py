"""Каталог известных бесплатных моделей OpenRouter для UI и дефолтов конфига."""

from __future__ import annotations

# Актуальность id проверяйте на https://openrouter.ai/models?order=top-free
OPENROUTER_FREE_MODELS: tuple[str, ...] = (
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "poolside/laguna-m.1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "openai/gpt-oss-120b:free",
    "qwen/qwen3-coder:free",
    "z-ai/glm-4.5-air:free",
    "baidu/cobuddy:free",
    "google/gemma-4-31b-it:free",
)

DEFAULT_FALLBACK_MODELS: list[str] = [
    m for m in OPENROUTER_FREE_MODELS if m != OPENROUTER_FREE_MODELS[0]
]


def merge_openrouter_model_options(
    primary: str | None,
    fallbacks: list[str] | None = None,
    *,
    catalog: tuple[str, ...] = OPENROUTER_FREE_MODELS,
) -> list[str]:
    """
    Список id моделей для UI: сначала primary и fallback из config.yaml, затем каталог.

    Модели из конфига не отбрасываются, даже если их нет в ``OPENROUTER_FREE_MODELS``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for mid in (primary, *(fallbacks or []), *catalog):
        m = (mid or "").strip()
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out
