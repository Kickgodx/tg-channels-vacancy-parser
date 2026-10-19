"""Лимит одновременных запросов API: под перегрузкой — быстрый 503, а не зависание."""

from __future__ import annotations

import time

import anyio
import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from prometheus_client import REGISTRY

from backend.overload import OverloadMiddleware, RequestSlots


def _app(limit: int, queue_timeout: float):
    app = FastAPI()
    app.add_middleware(OverloadMiddleware)
    app.state.request_slots = RequestSlots(limit, queue_timeout)
    gate = anyio.Event()
    started = anyio.Event()

    @app.get("/api/v1/slow")
    async def slow():
        started.set()
        await gate.wait()
        return {"ok": True}

    @app.get("/api/v1/fast")
    async def fast():
        return {"ok": True}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/events")
    async def events():
        async def stream():
            started.set()
            yield "data: 1\n\n"
            await gate.wait()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/download")
    async def download():
        async def stream():
            started.set()
            yield b"{"
            await gate.wait()
            yield b"}"

        return StreamingResponse(stream(), media_type="application/json")

    return app, gate, started


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _while_busy(path: str, check, *, limit=1, queue_timeout=0.2):
    """Занять все места запросом ``path`` и выполнить ``check(client)``; затем отпустить."""
    app, gate, started = _app(limit, queue_timeout)
    async with _client(app) as client, anyio.create_task_group() as tg:
        tg.start_soon(client.get, path)
        with anyio.fail_after(5):
            await started.wait()
        try:
            await check(client)
        finally:
            gate.set()
    return app


def test_busy_process_rejects_after_queue_timeout():
    async def check(client):
        t0 = time.monotonic()
        r = await client.get("/api/v1/fast")
        waited = time.monotonic() - t0
        assert r.status_code == 503
        assert r.headers["retry-after"] == "1"
        assert r.json()["detail"]["code"] == "OVERLOADED"
        assert r.json()["detail"]["retryable"] is True
        assert 0.15 <= waited < 2

    app = anyio.run(_while_busy, "/api/v1/slow", check)
    assert app.state.request_slots.in_use == 0  # места вернулись


def _rejections() -> float:
    return REGISTRY.get_sample_value("api_overload_rejections_total") or 0.0


def test_overload_rejection_is_counted():
    before = _rejections()

    async def check(client):
        assert (await client.get("/api/v1/fast")).status_code == 503

    anyio.run(_while_busy, "/api/v1/slow", check)
    assert _rejections() == before + 1


def test_served_request_is_not_counted_as_rejection():
    app, _gate, _started = _app(limit=1, queue_timeout=0.2)
    before = _rejections()

    async def run():
        async with _client(app) as client:
            assert (await client.get("/api/v1/fast")).status_code == 200

    anyio.run(run)
    assert _rejections() == before


def test_zero_queue_timeout_rejects_immediately():
    async def check(client):
        t0 = time.monotonic()
        assert (await client.get("/api/v1/fast")).status_code == 503
        assert time.monotonic() - t0 < 0.5

    anyio.run(lambda: _while_busy("/api/v1/slow", check, queue_timeout=0))


def test_queued_request_runs_when_slot_frees():
    async def scenario():
        app, gate, started = _app(1, 5)
        async with _client(app) as client, anyio.create_task_group() as tg:
            tg.start_soon(client.get, "/api/v1/slow")
            await started.wait()
            result = {}

            async def queued():
                result["r"] = await client.get("/api/v1/fast")

            tg.start_soon(queued)
            await anyio.sleep(0.1)
            assert "r" not in result  # ждёт в очереди
            gate.set()
        assert result["r"].status_code == 200

    anyio.run(scenario)


def test_health_skips_the_queue():
    async def check(client):
        assert (await client.get("/api/v1/health")).status_code == 200

    anyio.run(lambda: _while_busy("/api/v1/slow", check, queue_timeout=5))


def test_sse_stream_frees_its_slot():
    async def check(client):
        assert (await client.get("/api/v1/fast")).status_code == 200

    anyio.run(lambda: _while_busy("/api/v1/events", check))


def test_streamed_download_keeps_its_slot():
    async def check(client):
        assert (await client.get("/api/v1/fast")).status_code == 503

    anyio.run(lambda: _while_busy("/api/v1/download", check))


def test_without_slots_nothing_is_limited():
    async def scenario():
        app, gate, started = _app(1, 0)
        del app.state.request_slots
        async with _client(app) as client, anyio.create_task_group() as tg:
            tg.start_soon(client.get, "/api/v1/slow")
            await started.wait()
            assert (await client.get("/api/v1/fast")).status_code == 200
            gate.set()

    anyio.run(scenario)


def test_api_process_limits_requests_to_its_pool(api_client):
    from backend.db.session import api_thread_limit
    from backend.main import app
    from backend.settings import get_settings

    slots = app.state.request_slots
    assert slots.limit == api_thread_limit(get_settings())
    assert slots.queue_timeout == get_settings().api_queue_timeout_seconds
    assert api_client.get("/api/v1/health").status_code == 200
    assert slots.in_use == 0
