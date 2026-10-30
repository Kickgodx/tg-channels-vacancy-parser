"""Экспорт полного результата в JSON."""

from __future__ import annotations

import json

from jobdesk.models.parse_result import ParseResult


def export_json_bytes(result: ParseResult, *, extra: dict | None = None) -> bytes:
    """JSON результата; ``extra`` — дополнительные ключи верхнего уровня (например, пометка об обрезке)."""
    payload = {
        "run_params": result.run_params,
        "parsed_at": result.parsed_at.isoformat(),
        "parse_warnings": result.parse_warnings,
        "posts_fetched": result.posts_fetched,
        "vacancies_all": [v.model_dump(mode="json") for v in result.vacancies_all],
        "vacancies_unique": [v.model_dump(mode="json") for v in result.vacancies_unique],
        "duplicates": [v.model_dump(mode="json") for v in result.duplicates],
        "metrics": result.metrics.model_dump(mode="json"),
        **(extra or {}),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
