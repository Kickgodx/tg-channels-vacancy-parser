"""Загрузка единого каталога моделей из ``llm_models.yaml``."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from tg_jobs_parser.ai.models_catalog import LlmModelEntry, catalog_model_ids, dedupe_model_ids


class LlmModelsRegistry(BaseModel):
    """Содержимое ``llm_models.yaml``."""

    default_model: str = Field(
        default="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        min_length=1,
    )
    default_fallbacks: list[str] = Field(default_factory=list)
    models: list[LlmModelEntry] = Field(min_length=1)

    def catalog(self) -> list[LlmModelEntry]:
        return list(self.models)

    def ids(self) -> set[str]:
        return set(catalog_model_ids(self.models))


class LlmModelsLoadError(RuntimeError):
    """Ошибка чтения или валидации ``llm_models.yaml``."""


def default_llm_models_path() -> Path:
    """Путь к ``llm_models.yaml`` в корне репозитория (рядом с ``config.yaml``)."""
    root = Path(__file__).resolve().parents[2]
    return root / "llm_models.yaml"


def resolve_llm_models_path(
    config_path: str | Path | None = None,
    *,
    explicit: str | Path | None = None,
) -> Path:
    """
    Порядок поиска:
    1. ``explicit`` или env ``LLM_MODELS_PATH``
    2. ``llm_models.yaml`` в каталоге ``config.yaml``
    3. корень проекта (``default_llm_models_path()``)
    """
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p).resolve()

    env = os.getenv("LLM_MODELS_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p).resolve()

    if config_path:
        cfg_dir = Path(config_path).expanduser().resolve().parent
        candidate = cfg_dir / "llm_models.yaml"
        if candidate.is_file():
            return candidate

    return default_llm_models_path()


@lru_cache(maxsize=4)
def _load_registry_cached(resolved_path: str, mtime_ns: int) -> LlmModelsRegistry:
    _ = mtime_ns
    path = Path(resolved_path)
    if not path.is_file():
        raise LlmModelsLoadError(
            f"Каталог моделей не найден: {path}. Создайте llm_models.yaml в корне проекта."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise LlmModelsLoadError(f"Некорректный YAML в {path}: {e}") from e
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise LlmModelsLoadError(f"Корень {path} должен быть объектом (mapping).")
    try:
        return LlmModelsRegistry.model_validate(raw)
    except ValidationError as e:
        raise LlmModelsLoadError(str(e)) from e


def load_llm_models_registry(
    config_path: str | Path | None = None,
    *,
    models_path: str | Path | None = None,
) -> LlmModelsRegistry:
    """Загрузить ``llm_models.yaml`` (с кэшем по пути и mtime)."""
    path = resolve_llm_models_path(config_path, explicit=models_path)
    mtime_ns = path.stat().st_mtime_ns if path.is_file() else 0
    return _load_registry_cached(str(path.resolve()), mtime_ns)


def load_llm_model_catalog(
    config_path: str | Path | None = None,
    *,
    models_path: str | Path | None = None,
) -> list[LlmModelEntry]:
    return load_llm_models_registry(config_path, models_path=models_path).catalog()


def registry_signature(registry_path: Path, registry: LlmModelsRegistry) -> str:
    """Подпись каталога для сброса виджетов Streamlit."""
    try:
        mtime_ns = registry_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    models_part = "|".join(f"{e.id}:{e.name}:{e.cost}" for e in registry.models)
    fb = "|".join(dedupe_model_ids(registry.default_fallbacks))
    return f"{registry_path.resolve()}|{mtime_ns}|{registry.default_model}|{fb}|{models_part}"


def apply_ai_defaults_from_registry(
    ai_data: dict[str, Any],
    registry: LlmModelsRegistry,
) -> dict[str, Any]:
    """Подставить ``model`` / ``fallback_models`` из реестра, если не заданы в config.yaml."""
    data = dict(ai_data)
    if data.get("models") is not None:
        data.pop("models", None)
    ids = registry.ids()
    if not (data.get("model") or "").strip():
        data["model"] = registry.default_model
    model = str(data["model"]).strip()
    if model not in ids:
        raise LlmModelsLoadError(
            f"ai.model '{model}' отсутствует в llm_models.yaml. Доступные id: {', '.join(sorted(ids)[:8])}…"
        )
    if not data.get("fallback_models"):
        data["fallback_models"] = [
            m for m in registry.default_fallbacks if m in ids and m != model
        ]
    return data
