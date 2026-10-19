"""Лимит одновременных запросов процесса API: под перегрузкой — быстрый 503, а не зависание.

Sync-эндпоинт проходит через threadpool несколько раз, а сессия ``get_db`` держит
соединение между переходами. Если запросов в работе больше, чем потоков, все потоки
занимают запросы, ждущие соединение, а соединения — запросы, ждущие поток: тупик до
тайм-аута пула. Мест обработки столько же, сколько соединений (= потоков, см.
``api_thread_limit``): запрос никогда не ждёт поток, а лишние ждут здесь не дольше
``API_QUEUE_TIMEOUT_SECONDS`` и получают 503 ``OVERLOADED``.

Места создаёт lifespan (``app.state.request_slots``): у каждого процесса и каждого
``TestClient`` свои. Без них middleware ничего не ограничивает.
"""

from __future__ import annotations

import json

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.errors import CODE_OVERLOADED, error_detail

# Отвечают без очереди: liveness не должен падать из-за перегрузки.
EXEMPT_PATHS = frozenset({"/api/v1/health"})
RETRY_AFTER_SECONDS = 1

_BODY = json.dumps(
    {
        "detail": error_detail(
            CODE_OVERLOADED,
            "Сервер перегружен, повторите запрос через несколько секунд",
            retryable=True,
        )
    },
    ensure_ascii=False,
).encode()


class RequestSlots:
    """Места обработки запросов процесса: FIFO-очередь с ограниченным ожиданием."""

    def __init__(self, limit: int, queue_timeout: float) -> None:
        self.limit = limit
        self.queue_timeout = queue_timeout
        self._sem = anyio.Semaphore(limit)

    @property
    def in_use(self) -> int:
        return self.limit - self._sem.value

    async def acquire(self) -> bool:
        """Занять место; ``False`` — не освободилось за ``queue_timeout``."""
        if self.queue_timeout <= 0:
            try:
                self._sem.acquire_nowait()
            except anyio.WouldBlock:
                return False
            return True
        # Отмена по тайм-ауту безопасна: место, выданное в этот момент, семафор вернёт сам.
        with anyio.move_on_after(self.queue_timeout):
            await self._sem.acquire()
            return True
        return False

    def release(self) -> None:
        self._sem.release()


def _is_event_stream(message: Message) -> bool:
    for name, value in message.get("headers", ()):
        if name.lower() == b"content-type":
            return value.split(b";", 1)[0].strip().lower() == b"text/event-stream"
    return False


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_BODY)).encode()),
                (b"retry-after", str(RETRY_AFTER_SECONDS).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _BODY})


class OverloadMiddleware:
    """Пускает в приложение не больше ``request_slots.limit`` запросов одновременно."""

    def __init__(self, app: ASGIApp, *, exempt_paths: frozenset[str] = EXEMPT_PATHS) -> None:
        self.app = app
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        app = scope.get("app")
        slots: RequestSlots | None = getattr(getattr(app, "state", None), "request_slots", None)
        if scope["type"] != "http" or slots is None or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        if not await slots.acquire():
            await _reject(send)
            return

        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                slots.release()

        async def send_wrapper(message: Message) -> None:
            # SSE держит HTTP-соединение минутами, а БД читает короткими сессиями —
            # место ему после начала ответа не нужно. Экспорт (с открытой сессией) держит.
            if message["type"] == "http.response.start" and _is_event_stream(message):
                release()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            release()
