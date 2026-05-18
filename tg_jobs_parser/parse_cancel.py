"""Кооперативная отмена прогона парсинга (Ctrl+C, кнопка в UI)."""

from __future__ import annotations

import signal
import threading
from typing import Callable


class ParseCancelled(Exception):
    """Прогон остановлен по запросу пользователя."""


class ParseCancellationToken:
    """Потокобезопасный флаг отмены; проверяется в циклах fetch/process/LLM."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request_cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.is_cancelled():
            raise ParseCancelled()


def install_sigint_cancel_handler(
    token: ParseCancellationToken,
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """
    Первый Ctrl+C — мягкая отмена (``token``), второй — ``KeyboardInterrupt``.
    Регистрируется только в главном потоке (CLI / Streamlit main).
    """
    if threading.current_thread() is not threading.main_thread():
        return

    def _handler(signum: int, frame: object | None) -> None:
        if token.is_cancelled():
            raise KeyboardInterrupt
        token.request_cancel()
        if log is not None:
            log("Запрошена остановка. Повторите Ctrl+C для немедленного выхода.")

    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        pass
