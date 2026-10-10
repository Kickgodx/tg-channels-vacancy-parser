"""Кэш ответов LLM: PostgreSQL (через registered adapter) или файлы."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import AiConfig


def cache_key(
    *,
    text: str,
    model: str,
    prompt_version: str,
    mode: str,
) -> str:
    payload = f"{prompt_version}\0{model}\0{mode}\0{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AIExtractionCacheBackend(Protocol):
    def get(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
    ) -> VacancyAIExtraction | None: ...

    def put(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
        extraction: VacancyAIExtraction,
    ) -> None: ...


class _NullAIExtractionCache:
    def get(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
    ) -> VacancyAIExtraction | None:
        return None

    def put(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
        extraction: VacancyAIExtraction,
    ) -> None:
        return None


class FileAIExtractionCache:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def get(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
    ) -> VacancyAIExtraction | None:
        path = self._path(text, model, prompt_version, mode)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return VacancyAIExtraction.model_validate(data)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def put(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
        extraction: VacancyAIExtraction,
    ) -> None:
        path = self._path(text, model, prompt_version, mode)
        path.write_text(
            extraction.model_dump_json(indent=0),
            encoding="utf-8",
        )

    def _path(self, text: str, model: str, prompt_version: str, mode: str) -> Path:
        key = cache_key(text=text, model=model, prompt_version=prompt_version, mode=mode)
        return self._root / f"{key}.json"


class AIExtractionCache:
    """Фасад: делегирует в выбранный backend по ``AiConfig``."""

    def __init__(
        self,
        ai: AiConfig,
        backend: AIExtractionCacheBackend | None = None,
    ) -> None:
        self._backend = backend if backend is not None else build_extraction_cache(ai)

    def get(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
    ) -> VacancyAIExtraction | None:
        return self._backend.get(
            text=text,
            model=model,
            prompt_version=prompt_version,
            mode=mode,
        )

    def put(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
        extraction: VacancyAIExtraction,
    ) -> None:
        self._backend.put(
            text=text,
            model=model,
            prompt_version=prompt_version,
            mode=mode,
            extraction=extraction,
        )


def build_extraction_cache(ai: AiConfig) -> AIExtractionCacheBackend:
    if not ai.use_cache:
        return _NullAIExtractionCache()
    backend = (ai.cache_backend or "postgres").strip().lower()
    if backend == "file":
        return FileAIExtractionCache(Path(ai.cache_path))
    if backend == "postgres":
        from tg_jobs_parser.ai.cache_registry import get_postgres_cache_factory

        factory = get_postgres_cache_factory()
        if factory is None:
            raise ValueError(
                "ai.cache_backend='postgres', но Postgres-адаптер не зарегистрирован. "
                "Вызовите register_postgres_cache_factory() при старте backend/CLI "
                "(например backend.db.ai_cache.register_db_ai_cache)."
            )
        return factory(ai)
    raise ValueError(f"ai.cache_backend must be 'postgres' or 'file', got {ai.cache_backend!r}")
