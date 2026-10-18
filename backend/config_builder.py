"""Сборка AppConfig из config.yaml + overrides API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.schemas import RunCreateRequest
from tg_jobs_parser.ai.models_catalog import catalog_model_ids
from tg_jobs_parser.config.loader import ConfigLoadError, load_config
from tg_jobs_parser.config.schema import (
    AiConfig,
    AppConfig,
    FiltersConfig,
    ParserConfig,
    SearchConfig,
    SourcesConfig,
)


class RunOverrideError(ConfigLoadError):
    """Override прогона недопустим (ошибка клиента, 400) — в отличие от сломанного config.yaml (500)."""


def _reject_unknown_models(base: AppConfig, ai: dict[str, Any]) -> None:
    """Модели override — только из каталога ``llm_models.yaml`` или уже заданные в config.yaml."""
    allowed = {*catalog_model_ids(base.llm_model_catalog), base.ai.model, *base.ai.fallback_models}
    requested = [ai.get("model"), *(ai.get("fallback_models") or [])]
    unknown = sorted({m for m in requested if m and m not in allowed})
    if unknown:
        raise RunOverrideError(f"Модель не из llm_models.yaml: {', '.join(unknown)}")


def build_config(base_path: Path, body: RunCreateRequest) -> AppConfig:
    base = load_config(base_path)
    data = base.model_dump()

    if body.channels:
        data["sources"] = SourcesConfig(channels=list(dict.fromkeys(body.channels))).model_dump()

    if body.search:
        merged = {**data.get("search", {}), **body.search.model_dump(exclude_none=True)}
        data["search"] = SearchConfig(**merged).model_dump()

    if body.filters:
        merged = {**data.get("filters", {}), **body.filters.model_dump(exclude_none=True)}
        data["filters"] = FiltersConfig(**merged).model_dump()

    if body.parser:
        merged = {**data.get("parser", {}), **body.parser.model_dump(exclude_none=True)}
        data["parser"] = ParserConfig(**merged).model_dump()

    if body.ai:
        overrides = body.ai.model_dump(exclude_none=True)
        _reject_unknown_models(base, overrides)
        merged = {**data.get("ai", {}), **overrides}
        data["ai"] = AiConfig(**merged).model_dump()

    return AppConfig.model_validate(data)


def config_snapshot_dict(config: AppConfig) -> dict[str, Any]:
    return config.to_serializable_dict()
