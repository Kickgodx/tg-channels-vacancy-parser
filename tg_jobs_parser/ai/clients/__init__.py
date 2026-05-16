"""Клиенты LLM-провайдеров (ленивый импорт)."""

from __future__ import annotations

from typing import Any

__all__ = ["OpenRouterClient"]


def __getattr__(name: str) -> Any:
    if name == "OpenRouterClient":
        from tg_jobs_parser.ai.clients.openrouter import OpenRouterClient

        return OpenRouterClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
