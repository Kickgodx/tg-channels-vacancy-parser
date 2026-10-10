"""Кооперативная отмена прогона парсинга (Ctrl+C, кнопка в UI)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import threading
from collections.abc import Callable


class ParseCancelled(Exception):
    """Прогон остановлен по запросу пользователя."""


class ParseCancellationToken:
    """Потокобезопасный флаг отмены; проверяется в циклах fetch/process/LLM."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._async_lock = threading.Lock()
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._async_tasks: list[asyncio.Task] = []

    def bind_async_tasks(
        self,
        loop: asyncio.AbstractEventLoop,
        tasks: list[asyncio.Task],
    ) -> None:
        """Регистрирует asyncio-задачи Telethon для прерывания из другого потока (кнопка «Отменить»)."""
        with self._async_lock:
            self._async_loop = loop
            self._async_tasks = list(tasks)

    def clear_async_tasks(self) -> None:
        with self._async_lock:
            self._async_loop = None
            self._async_tasks = []

    def request_cancel(self) -> None:
        self._event.set()
        with self._async_lock:
            loop = self._async_loop
            tasks = list(self._async_tasks)
        if loop is None or not tasks:
            return

        def _cancel_all() -> None:
            for task in tasks:
                if not task.done():
                    task.cancel()

        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_cancel_all)

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

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, _handler)
