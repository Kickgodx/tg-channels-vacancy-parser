"""Лидер процессов API: общую работу выполняет один процесс (platform/data-maintenance)."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from backend.leader import ApiLeader, LeaderLock


class _Duties:
    def __init__(self) -> None:
        self.acquired = 0
        self.lost = 0
        self.ticks = 0

    def leader(self, interval_s: float = 30.0) -> ApiLeader:
        return ApiLeader(
            on_acquire=self._acquire,
            on_lose=self._lose,
            on_tick=self._tick,
            interval_s=interval_s,
        )

    def _acquire(self) -> None:
        self.acquired += 1

    def _lose(self) -> None:
        self.lost += 1

    def _tick(self) -> None:
        self.ticks += 1


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_only_one_process_becomes_leader(pg_db):
    first, second = _Duties(), _Duties()
    a, b = first.leader(), second.leader()
    try:
        assert a.try_become_leader() is True
        assert b.try_become_leader() is False
        assert (first.acquired, second.acquired) == (1, 0)
        assert a.try_become_leader() is True  # повтор у лидера не перезапускает работу
        assert first.acquired == 1
    finally:
        a.stop()
        b.stop()
    assert first.lost == 1  # остановка лидера снимает обязанности
    assert second.lost == 0


def test_leadership_moves_when_leader_process_dies(pg_db):
    first, second = _Duties(), _Duties()
    a = first.leader()
    b = second.leader(interval_s=0.05)
    try:
        assert a.try_become_leader()
        b.start()
        time.sleep(0.2)
        assert second.acquired == 0
        # «Процесс упал»: его сессия Postgres закрыта — lock снят сервером.
        a._lock._conn.invalidate()
        assert _wait(lambda: b.is_leader)
        assert second.acquired == 1
    finally:
        b.stop()
        a.stop()


def test_leader_drops_role_when_its_connection_is_killed(pg_db):
    duties = _Duties()
    leader = duties.leader(interval_s=0.05)
    other = LeaderLock()
    try:
        assert leader.try_become_leader()
        leader.start()
        assert _wait(lambda: duties.ticks > 0)  # лидер выполняет периодическую работу
        pid = leader._lock._conn.execute(text("SELECT pg_backend_pid()")).scalar()
        assert other.try_acquire() is False
        with other._get_engine().connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
        assert _wait(lambda: duties.lost == 1)
        assert leader.is_leader is False
        # Роль снова свободна; цикл лидера заберёт её обратно, если её не взял другой.
        assert _wait(lambda: leader.is_leader or other.try_acquire())
    finally:
        leader.stop()
        other.release()


def test_startup_cleanup_skipped_when_another_process_is_leader(monkeypatch, pg_db, default_test_config):
    from fastapi.testclient import TestClient

    import backend.main as main
    from backend.settings import get_settings

    monkeypatch.setenv("CONFIG_PATH", str(default_test_config))
    monkeypatch.setenv("JWT_SECRET", "test-secret-value-for-tests")
    get_settings.cache_clear()
    calls: list[int] = []
    monkeypatch.setattr(main, "_periodic_cleanup", lambda: calls.append(1))

    holder = LeaderLock()
    assert holder.try_acquire()
    try:
        with TestClient(main.app) as client:
            assert client.get("/api/v1/health").status_code == 200
        assert calls == []
    finally:
        holder.release()

    with TestClient(main.app):
        pass
    assert calls == [1]  # лидер свободен — очистка при старте выполнена один раз
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_engine_after():
    yield
    from backend.db.session import reset_engine

    reset_engine()
