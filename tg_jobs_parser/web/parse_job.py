"""Состояние фонового прогона парсинга для Streamlit (без доступа к session_state из worker)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.parse_cancel import ParseCancellationToken
from tg_jobs_parser.parse_progress import ParseProgressSnapshot


@dataclass
class ParseJobState:
    """
    Разделяемое между главным потоком Streamlit и worker-потоком состояние.
    Worker только пишет сюда; UI на каждом rerun читает и синхронизирует в ``session_state``.
    """

    cancel_token: ParseCancellationToken
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    running: bool = True
    progress: ParseProgressSnapshot | None = None
    result: ParseResult | None = None
    error: str | None = None
    ui_message: str | None = None
    xlsx_path: Path | None = None
    json_path: Path | None = None
    thread: threading.Thread | None = field(default=None, repr=False)

    def set_progress(self, snap: ParseProgressSnapshot) -> None:
        with self._lock:
            self.progress = snap

    def finish_success(
        self,
        result: ParseResult,
        *,
        xlsx_path: Path,
        json_path: Path,
        ui_message: str | None = None,
    ) -> None:
        with self._lock:
            self.running = False
            self.result = result
            self.xlsx_path = xlsx_path
            self.json_path = json_path
            self.ui_message = ui_message

    def finish_error(self, message: str) -> None:
        with self._lock:
            self.running = False
            self.error = message

    def is_running(self) -> bool:
        with self._lock:
            return self.running

    def snapshot(
        self,
    ) -> tuple[
        bool,
        ParseProgressSnapshot | None,
        ParseResult | None,
        str | None,
        str | None,
        Path | None,
        Path | None,
    ]:
        with self._lock:
            return (
                self.running,
                self.progress,
                self.result,
                self.error,
                self.ui_message,
                self.xlsx_path,
                self.json_path,
            )

    def mark_finished_if_thread_dead(self) -> None:
        """Если worker упал без ``finish_*``, снять флаг running (зависший UI)."""
        if self.thread is None or self.thread.is_alive():
            return
        with self._lock:
            if self.running:
                self.running = False
                if self.error is None and self.result is None:
                    self.error = "Поток парсинга завершился неожиданно."
