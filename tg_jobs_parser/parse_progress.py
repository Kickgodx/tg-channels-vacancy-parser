"""Снимки прогресса парсинга для UI и частичного экспорта."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tg_jobs_parser.models.vacancy import Vacancy

ParseProgressCallback = Callable[["ParseProgressSnapshot"], None]


@dataclass
class ParseProgressSnapshot:
    """Промежуточное состояние во время ``run_parse`` (до финальной дедупликации)."""

    posts_processed: int
    posts_total: int
    vacancies_found: int
    vacancies: list[Vacancy] = field(default_factory=list)
    phase: str = "processing"
    ai_llm_disabled: bool = False
    ai_llm_disabled_reason: str | None = None
    ai_tokens_in: int = 0
    ai_tokens_out: int = 0
    ai_skipped_score: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def save_partial_batch(
    directory: Path,
    snapshot: ParseProgressSnapshot,
    *,
    run_id: str | None = None,
) -> Path:
    """Сохраняет JSON с вакансиями на текущий момент (инкрементальный батч)."""
    directory.mkdir(parents=True, exist_ok=True)
    rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = directory / f"partial_{rid}_{snapshot.posts_processed:05d}.json"
    payload = {
        "posts_processed": snapshot.posts_processed,
        "posts_total": snapshot.posts_total,
        "vacancies_found": snapshot.vacancies_found,
        "phase": snapshot.phase,
        "ai_llm_disabled": snapshot.ai_llm_disabled,
        "ai_llm_disabled_reason": snapshot.ai_llm_disabled_reason,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "vacancies": [v.model_dump(mode="json") for v in snapshot.vacancies],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
