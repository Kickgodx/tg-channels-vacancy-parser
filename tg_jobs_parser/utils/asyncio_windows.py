"""Смягчение шумных asyncio-ошибок при закрытии сокетов на Windows."""

from __future__ import annotations

import asyncio
import logging
import sys


class _AsyncioConnectionResetFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and isinstance(record.exc_info[1], ConnectionResetError):
            return False
        msg = record.getMessage()
        return "_call_connection_lost" not in msg


def install_windows_asyncio_cleanup_helpers() -> None:
    """Не падаем в ERROR при RST сокета; не мешает нормальным asyncio-ошибкам."""
    if sys.platform != "win32":
        return

    asyncio_logger = logging.getLogger("asyncio")
    if not any(isinstance(f, _AsyncioConnectionResetFilter) for f in asyncio_logger.filters):
        asyncio_logger.addFilter(_AsyncioConnectionResetFilter())

    def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return
        loop.default_exception_handler(context)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    if loop.get_exception_handler() is None:
        loop.set_exception_handler(_handler)
