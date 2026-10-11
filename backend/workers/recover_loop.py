"""Периодический recover stuck jobs в RQ-воркерах."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

_LOG = logging.getLogger(__name__)


def start_periodic_recover(
    recover_fn: Callable[[], int],
    *,
    interval_seconds: int,
    name: str,
) -> threading.Event:
    """Запустить daemon-thread; вернуть Event для остановки."""
    stop = threading.Event()
    interval = max(15, int(interval_seconds))

    def _loop() -> None:
        while not stop.wait(timeout=interval):
            try:
                n = recover_fn()
                if n:
                    _LOG.info("%s: periodic recover requeued=%s", name, n)
            except Exception as e:
                _LOG.warning("%s: periodic recover failed: %s", name, e)

    threading.Thread(target=_loop, name=name, daemon=True).start()
    return stop
