"""Экспорт полного результата в JSON."""

from __future__ import annotations

import json
from pathlib import Path

from tg_jobs_parser.models.parse_result import ParseResult


def export_json(result: ParseResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_params": result.run_params,
        "parsed_at": result.parsed_at.isoformat(),
        "parse_warnings": result.parse_warnings,
        "posts_fetched": result.posts_fetched,
        "vacancies_all": [v.model_dump(mode="json") for v in result.vacancies_all],
        "vacancies_unique": [v.model_dump(mode="json") for v in result.vacancies_unique],
        "duplicates": [v.model_dump(mode="json") for v in result.duplicates],
        "metrics": result.metrics.model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_json_bytes(result: ParseResult) -> bytes:
    payload = {
        "run_params": result.run_params,
        "parsed_at": result.parsed_at.isoformat(),
        "parse_warnings": result.parse_warnings,
        "posts_fetched": result.posts_fetched,
        "vacancies_all": [v.model_dump(mode="json") for v in result.vacancies_all],
        "vacancies_unique": [v.model_dump(mode="json") for v in result.vacancies_unique],
        "duplicates": [v.model_dump(mode="json") for v in result.duplicates],
        "metrics": result.metrics.model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def load_parse_result_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
