"""Дисковый кэш ответов LLM."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tg_jobs_parser.ai.schema import VacancyAIExtraction


def _cache_key(
    *,
    text: str,
    model: str,
    prompt_version: str,
    mode: str,
) -> str:
    payload = f"{prompt_version}\0{model}\0{mode}\0{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AIExtractionCache:
    def __init__(self, root: Path, *, enabled: bool) -> None:
        self._root = root
        self._enabled = enabled
        if enabled:
            self._root.mkdir(parents=True, exist_ok=True)

    def get(
        self,
        *,
        text: str,
        model: str,
        prompt_version: str,
        mode: str,
    ) -> VacancyAIExtraction | None:
        if not self._enabled:
            return None
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
        if not self._enabled:
            return
        path = self._path(text, model, prompt_version, mode)
        path.write_text(
            extraction.model_dump_json(indent=0),
            encoding="utf-8",
        )

    def _path(self, text: str, model: str, prompt_version: str, mode: str) -> Path:
        key = _cache_key(text=text, model=model, prompt_version=prompt_version, mode=mode)
        return self._root / f"{key}.json"
