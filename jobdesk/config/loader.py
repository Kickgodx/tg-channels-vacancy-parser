"""Загрузка и слияние YAML-конфига."""

from __future__ import annotations

import os
import threading
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from jobdesk.ai.llm_models_registry import (
    LlmModelsLoadError,
    apply_ai_defaults_from_registry,
    load_llm_models_registry,
    resolve_llm_models_path,
)
from jobdesk.config.schema import AppConfig


class ConfigLoadError(RuntimeError):
    """Ошибка чтения или валидации конфигурации."""


# Устаревшие ключи, которые переносит ``AiConfig.migrate_fallback_model``.
_LEGACY_KEYS = frozenset({"ai.models", "ai.fallback_model"})
# Поля схемы, которые заполняет загрузчик, а не YAML: значение из файла молча перезаписалось бы.
_LOADER_FIELDS = frozenset({"llm_model_catalog"})


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """Вложенная модель поля (в том числе ``Model | None``); поля-словари и списки не обходятся."""
    for candidate in (annotation, *typing.get_args(annotation)):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _unknown_keys(model: type[BaseModel], data: dict[str, Any], prefix: str = "") -> list[str]:
    """Пути ключей YAML, которых нет в схеме: опечатка иначе молча оставила бы умолчание."""
    out: list[str] = []
    fields = model.model_fields
    for key, value in data.items():
        path = f"{prefix}{key}"
        if path in _LEGACY_KEYS:
            continue
        field = fields.get(key) if isinstance(key, str) else None
        if field is None or (not prefix and key in _LOADER_FIELDS):
            out.append(path)
            continue
        nested = _nested_model(field.annotation)
        if nested is not None and isinstance(value, dict):
            out.extend(_unknown_keys(nested, value, f"{path}."))
    return out


@dataclass(frozen=True)
class _CachedConfig:
    config_sig: tuple[int, int]
    models_explicit: str | None
    models_path: Path
    models_sig: tuple[int, int] | None
    config: AppConfig


_cache_lock = threading.Lock()
_cache: dict[str, _CachedConfig] = {}


def _file_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def load_config(path: str | Path) -> AppConfig:
    """Загружает ``config.yaml`` и возвращает ``AppConfig``.

    Результат кэшируется по mtime/размеру ``config.yaml`` и ``llm_models.yaml``:
    функцию зовут на каждый запрос метрик, батч upsert и постановку джобы, а разбор
    YAML с валидацией стоит ~5 мс. Правка любого из файлов подхватывается сразу.
    Возвращается копия — вызывающий может менять её без влияния на кэш.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigLoadError(f"Файл конфигурации не найден: {path}")
    key = f"{path.resolve()}|{os.getcwd()}|{os.getenv('LLM_MODELS_PATH', '')}"
    config_sig = _file_sig(path)
    with _cache_lock:
        hit = _cache.get(key)
    if (
        hit is not None
        and hit.config_sig == config_sig
        and resolve_llm_models_path(path, explicit=hit.models_explicit) == hit.models_path
        and _file_sig(hit.models_path) == hit.models_sig
    ):
        return hit.config.model_copy(deep=True)

    cfg, models_explicit = _load_config_uncached(path)
    models_path = resolve_llm_models_path(path, explicit=models_explicit)
    if config_sig is not None:
        with _cache_lock:
            _cache[key] = _CachedConfig(
                config_sig=config_sig,
                models_explicit=models_explicit,
                models_path=models_path,
                models_sig=_file_sig(models_path),
                config=cfg,
            )
    return cfg.model_copy(deep=True)


def _load_config_uncached(path: Path) -> tuple[AppConfig, str | None]:
    """Разбор и валидация; второй элемент — ``ai.llm_models_path`` из YAML (для ключа кэша)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Некорректный YAML: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigLoadError("Корень YAML должен быть объектом (mapping).")
    unknown = _unknown_keys(AppConfig, raw)
    if unknown:
        raise ConfigLoadError(f"Неизвестные ключи config.yaml: {', '.join(unknown)}")
    try:
        ai_raw = raw.get("ai")
        ai_dict = dict(ai_raw) if isinstance(ai_raw, dict) else {}
        explicit_models = ai_dict.get("llm_models_path")
        try:
            registry = load_llm_models_registry(
                path,
                models_path=explicit_models,
            )
        except LlmModelsLoadError as e:
            raise ConfigLoadError(str(e)) from e
        raw = dict(raw)
        try:
            raw["ai"] = apply_ai_defaults_from_registry(ai_dict, registry)
        except LlmModelsLoadError as e:
            raise ConfigLoadError(str(e)) from e
        cfg = AppConfig.model_validate(raw)
        explicit = str(explicit_models) if explicit_models else None
        return cfg.model_copy(update={"llm_model_catalog": registry.catalog()}), explicit
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
