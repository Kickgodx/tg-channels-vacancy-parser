"""Сборка AppConfig из config.yaml + overrides API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import (
    AiConfig,
    AppConfig,
    FiltersConfig,
    SearchConfig,
    SourcesConfig,
    TelegramConfig,
)
from backend.schemas import RunCreateRequest


def build_config(base_path: Path, body: RunCreateRequest) -> AppConfig:
    base = load_config(base_path)
    data = base.model_dump()

    if body.channels:
        data["sources"] = SourcesConfig(channels=list(dict.fromkeys(body.channels))).model_dump()

    if body.search:
        merged = {**data.get("search", {}), **body.search}
        data["search"] = SearchConfig(**merged).model_dump()

    if body.filters:
        merged = {**data.get("filters", {}), **body.filters}
        data["filters"] = FiltersConfig(**merged).model_dump()

    if body.parser:
        p = dict(data.get("parser", {}))
        p.update(body.parser)
        data["parser"] = p

    if body.ai:
        merged = {**data.get("ai", {}), **body.ai}
        data["ai"] = AiConfig(**merged).model_dump()

    if body.telegram_session_file:
        merged_tg = {**data.get("telegram", {}), "telethon_session_file": body.telegram_session_file}
        data["telegram"] = TelegramConfig(**merged_tg).model_dump()

    return AppConfig.model_validate(data)


def config_snapshot_dict(config: AppConfig) -> dict[str, Any]:
    return config.to_serializable_dict()
