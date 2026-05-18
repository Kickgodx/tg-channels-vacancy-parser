"""In-memory состояние активных прогонов."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.parse_cancel import ParseCancellationToken
from tg_jobs_parser.parse_progress import ParseProgressSnapshot


@dataclass
class ActiveRun:
    run_id: uuid.UUID
    cancel_token: ParseCancellationToken = field(default_factory=ParseCancellationToken)
    status: str = "running"
    result: ParseResult | None = None
    error: str | None = None
    last_progress: ParseProgressSnapshot | None = None


class RunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: ActiveRun | None = None
        self._runs: dict[uuid.UUID, ActiveRun] = {}

    def has_active(self) -> bool:
        with self._lock:
            return self._active is not None and self._active.status == "running"

    def register(self, run_id: uuid.UUID) -> ActiveRun:
        with self._lock:
            if self._active is not None and self._active.status == "running":
                raise RuntimeError("Уже выполняется другой прогон парсинга")
            run = ActiveRun(run_id=run_id)
            self._active = run
            self._runs[run_id] = run
            return run

    def get(self, run_id: uuid.UUID) -> ActiveRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def finish(self, run_id: uuid.UUID) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            if self._active and self._active.run_id == run_id:
                self._active = None


run_registry = RunRegistry()
