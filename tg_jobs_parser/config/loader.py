"""Загрузка и слияние YAML-конфига."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from tg_jobs_parser.config.schema import AppConfig


class ConfigLoadError(RuntimeError):
    """Ошибка чтения или валидации конфигурации."""


def load_config(path: str | Path) -> AppConfig:
    """Загружает ``config.yaml`` и возвращает ``AppConfig``."""
    path = Path(path)
    if not path.is_file():
        raise ConfigLoadError(f"Файл конфигурации не найден: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Некорректный YAML: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigLoadError("Корень YAML должен быть объектом (mapping).")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigLoadError(str(e)) from e


def deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Поверхностное слияние для вложенных dict (для CLI override)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge_dict(result[k], v)
        else:
            result[k] = v
    return result
