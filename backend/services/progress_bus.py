"""Pub/sub прогресса парсинга для SSE (in-process, один backend-инстанс)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

# Любой сериализуемый снимок прогресса (сейчас — ParseProgressSnapshot).
Snapshot = Any


@dataclass
class _RunChannel:
    lock: threading.Lock = field(default_factory=threading.Lock)
    last: Snapshot | None = None
    subscribers: list[asyncio.Queue[Snapshot | None]] = field(default_factory=list)


class ProgressBus:
    def __init__(self) -> None:
        self._channels: dict[uuid.UUID, _RunChannel] = {}
        self._global_lock = threading.Lock()

    def _channel(self, run_id: uuid.UUID) -> _RunChannel:
        with self._global_lock:
            if run_id not in self._channels:
                self._channels[run_id] = _RunChannel()
            return self._channels[run_id]

    def publish(self, run_id: uuid.UUID, snapshot: Snapshot) -> None:
        ch = self._channel(run_id)
        with ch.lock:
            ch.last = snapshot
            dead: list[asyncio.Queue] = []
            for q in ch.subscribers:
                try:
                    q.put_nowait(snapshot)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                ch.subscribers.remove(q)

    def publish_done(self, run_id: uuid.UUID) -> None:
        ch = self._channel(run_id)
        with ch.lock:
            for q in ch.subscribers:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)

    def get_last(self, run_id: uuid.UUID) -> Snapshot | None:
        ch = self._channel(run_id)
        with ch.lock:
            return ch.last

    async def subscribe(self, run_id: uuid.UUID) -> asyncio.Queue[Snapshot | None]:
        q: asyncio.Queue[Snapshot | None] = asyncio.Queue(maxsize=32)
        ch = self._channel(run_id)
        with ch.lock:
            if ch.last is not None:
                await q.put(ch.last)
            ch.subscribers.append(q)
        return q

    def unsubscribe(self, run_id: uuid.UUID, q: asyncio.Queue) -> None:
        ch = self._channel(run_id)
        with ch.lock:
            if q in ch.subscribers:
                ch.subscribers.remove(q)


progress_bus = ProgressBus()
