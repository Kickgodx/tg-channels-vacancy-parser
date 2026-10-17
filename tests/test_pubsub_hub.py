"""SSE: один Redis-подписчик на процесс, heartbeat, fallback на опрос БД при сбое Redis."""

from __future__ import annotations

import asyncio
import json

import fakeredis
import fakeredis.aioredis
import pytest

from backend.api.routes import runs
from backend.db import parse_run_repository as pr
from backend.db.session import reset_engine, session_scope
from backend.queue import rq_conn
from backend.services.pubsub_hub import PubSubHub, PubSubUnavailable
from backend.settings import get_settings


@pytest.fixture
def fake_server(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(rq_conn, "get_async_redis_client", lambda: fakeredis.aioredis.FakeRedis(server=server))
    return server


def _publish(server, channel: str, payload: dict) -> None:
    fakeredis.FakeStrictRedis(server=server).publish(channel, json.dumps(payload))


def _numsub(server, channel: str) -> int:
    return dict(fakeredis.FakeStrictRedis(server=server).pubsub_numsub(channel)).get(channel.encode(), 0)


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.05)


def test_fan_out_with_single_redis_subscription(fake_server):
    async def scenario():
        hub = PubSubHub()
        async with hub.subscribe("ch") as a, hub.subscribe("ch") as b, hub.subscribe("other") as c:
            assert _numsub(fake_server, "ch") == 1  # одна подписка Redis на канал, не на стрим
            _publish(fake_server, "ch", {"n": 1})
            assert await a.get(timeout=2) == {"n": 1}
            assert await b.get(timeout=2) == {"n": 1}
            assert await c.get(timeout=0.2) is None
        await _settle()
        assert hub.channels == frozenset()
        assert hub._pubsub is None  # последний подписчик ушёл — соединение закрыто

    asyncio.run(scenario())


def test_release_keeps_channel_for_remaining_subscriber(fake_server):
    async def scenario():
        hub = PubSubHub()
        async with hub.subscribe("ch") as keep:
            async with hub.subscribe("ch"):
                pass
            async with hub.subscribe("gone"):
                pass
            await _settle()
            assert hub.channels == frozenset({"ch"})
            assert _numsub(fake_server, "gone") == 0
            _publish(fake_server, "ch", {"n": 2})
            assert await keep.get(timeout=2) == {"n": 2}

    asyncio.run(scenario())


def test_cancelled_stream_releases_subscription(fake_server):
    """Обрыв клиента отменяет стрим — подписка не должна утечь."""

    async def scenario():
        hub = PubSubHub()
        subscribed = asyncio.Event()

        async def stream():
            async with hub.subscribe("ch") as sub:
                subscribed.set()
                await sub.get(timeout=60)

        task = asyncio.create_task(stream())
        await subscribed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _settle()
        assert hub.channels == frozenset()
        assert hub._pubsub is None

    asyncio.run(scenario())


def test_redis_failure_wakes_subscribers(fake_server):
    async def scenario():
        hub = PubSubHub()
        async with hub.subscribe("ch") as sub:

            async def broken(**kwargs):
                raise ConnectionError("redis down")

            hub._pubsub.get_message = broken
            with pytest.raises(PubSubUnavailable):
                await sub.get(timeout=5)
        assert hub._pubsub is None

    asyncio.run(scenario())


def test_subscribe_without_redis_raises_unavailable(monkeypatch):
    def no_redis():
        raise RuntimeError("REDIS_URL не задан")

    monkeypatch.setattr(rq_conn, "get_async_redis_client", no_redis)

    async def scenario():
        with pytest.raises(PubSubUnavailable):
            async with PubSubHub().subscribe("ch"):
                pass

    asyncio.run(scenario())


@pytest.fixture
def running_run(monkeypatch, pg_db):
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    get_settings.cache_clear()
    reset_engine()
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="running").id
    yield rid
    reset_engine()
    get_settings.cache_clear()


def _complete(run_id) -> None:
    with session_scope() as session:
        pr.get_parse_run(session, run_id).status = "completed"


async def _collect(gen, *, until: str = "event: done", timeout: float = 10.0) -> list[str]:
    chunks: list[str] = []

    async def consume():
        async for chunk in gen:
            chunks.append(chunk)
            if chunk.startswith(until):
                return

    await asyncio.wait_for(consume(), timeout=timeout)
    return chunks


def test_redis_stream_sends_heartbeat_and_done(fake_server, running_run, monkeypatch):
    monkeypatch.setattr(runs, "HEARTBEAT_INTERVAL_S", 0.2)
    monkeypatch.setattr(runs, "PUBSUB_WAIT_S", 0.1)

    async def scenario():
        async def publish_later():
            await asyncio.sleep(0.6)
            _publish(fake_server, f"parse_run:{running_run}", {"event": "progress", "posts_processed": 4})
            await asyncio.sleep(0.1)
            _publish(fake_server, f"parse_run:{running_run}", {"event": "done"})

        pub = asyncio.create_task(publish_later())
        chunks = await _collect(runs._sse_events(running_run))
        await pub
        return chunks

    chunks = asyncio.run(scenario())
    assert ": ping\n\n" in chunks
    progress = [c for c in chunks if c.startswith("event: progress")]
    assert progress and '"posts_processed": 4' in progress[0]
    assert chunks[-1] == "event: done\ndata: {}\n\n"


def test_redis_failure_falls_back_to_db_poll(running_run, monkeypatch):
    """Redis недоступен — стрим не обрывается, а дочитывает статус из БД."""

    def no_redis():
        raise ConnectionError("redis down")

    monkeypatch.setattr(rq_conn, "get_async_redis_client", no_redis)
    monkeypatch.setattr(runs, "DB_POLL_INTERVAL_S", 0.05)

    async def scenario():
        async def complete_later():
            await asyncio.sleep(0.3)
            await asyncio.to_thread(_complete, running_run)

        task = asyncio.create_task(complete_later())
        chunks = await _collect(runs._sse_events(running_run))
        await task
        return chunks

    assert asyncio.run(scenario())[-1] == "event: done\ndata: {}\n\n"


def test_db_poll_has_no_time_cap_and_pings(running_run, monkeypatch):
    """Без Redis стрим живёт до терминального статуса (раньше молча обрывался через 300 с)."""
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()
    monkeypatch.setattr(runs, "DB_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(runs, "HEARTBEAT_INTERVAL_S", 0.05)

    async def scenario():
        async def complete_later():
            await asyncio.sleep(1.0)  # прежний fallback обрывался после 300 опросов
            await asyncio.to_thread(_complete, running_run)

        task = asyncio.create_task(complete_later())
        chunks = await _collect(runs._sse_events(running_run))
        await task
        return chunks

    chunks = asyncio.run(scenario())
    assert ": ping\n\n" in chunks
    assert chunks[-1] == "event: done\ndata: {}\n\n"
