"""Загрузка golden dataset для extraction evals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tg_jobs_parser.evals.paths import golden_extraction_path


def load_extraction_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """Читает JSONL: id, text, expected, tags (+ опционально search_context)."""
    p = path or golden_extraction_path()
    if not p.is_file():
        raise FileNotFoundError(f"Golden extraction dataset not found: {p}")
    cases: list[dict[str, Any]] = []
    with p.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:{line_no}: invalid JSON: {e}") from e
            if not isinstance(obj, dict) or "id" not in obj or "expected" not in obj:
                raise ValueError(
                    f"{p}:{line_no}: case must be an object with id and expected"
                )
            cases.append(obj)
    return cases
