"""Фоновое обслуживание каталога: расписание задач, исполнитель вне API (platform/runtime-config)."""

from __future__ import annotations

import threading

import pytest

from backend import maintenance
from backend.db import maintenance_requests
from backend.db.session import session_scope
from backend.leader import LeaderLock
from backend.workers import maintenance_worker


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def calls(monkeypatch):
    """Задачи обслуживания подменены счётчиками; интервал индексации — 300 с."""
    seen: list[str] = []
    monkeypatch.setattr(maintenance, "sweep_embeddings", lambda: seen.append("embeddings"))
    monkeypatch.setattr(maintenance, "ensure_digest", lambda: seen.append("digest"))
    monkeypatch.setattr(maintenance, "run_requested_map_build", lambda: seen.append("map-request"))
    monkeypatch.setattr(maintenance, "maybe_build_market_map", lambda: seen.append("map"))
    monkeypatch.setattr(maintenance, "_embedding_interval", lambda: 300.0)
    monkeypatch.setattr(maintenance, "_map_rebuild_pending", lambda: False)
    return seen


def test_duties_run_each_task_by_its_interval(calls):
    clock = _Clock()
    duties = maintenance.MaintenanceDuties(threaded=False, clock=clock)
    duties.tick()
    assert calls == ["embeddings", "digest", "map-request", "map"]

    calls.clear()
    clock.now += 60
    duties.tick()
    assert calls == []  # ни один интервал не прошёл

    clock.now += 300
    duties.tick()
    assert calls == ["embeddings"]

    calls.clear()
    clock.now += maintenance.MAP_CHECK_INTERVAL_S
    duties.tick()
    assert calls == ["embeddings", "map-request", "map"]


def test_map_request_runs_without_waiting_for_schedule(calls, monkeypatch):
    clock = _Clock()
    duties = maintenance.MaintenanceDuties(threaded=False, clock=clock)
    duties.tick()
    calls.clear()

    monkeypatch.setattr(maintenance, "_map_rebuild_pending", lambda: True)
    clock.now += 20
    duties.tick()
    # Только запрос администратора; проверка по расписанию не сдвинулась.
    assert calls == ["map-request"]
    monkeypatch.setattr(maintenance, "_map_rebuild_pending", lambda: False)
    calls.clear()
    clock.now += maintenance.MAP_CHECK_INTERVAL_S - 20
    duties.tick()
    assert "map" in calls


def test_threaded_duties_skip_task_still_running(monkeypatch):
    """Лидер API: задача в своём потоке; пока он жив, второй не запускается."""
    release = threading.Event()
    started: list[str] = []

    def slow_sweep():
        started.append("embeddings")
        release.wait(5)

    monkeypatch.setattr(maintenance, "sweep_embeddings", slow_sweep)
    monkeypatch.setattr(maintenance, "ensure_digest", lambda: None)
    monkeypatch.setattr(maintenance, "_map_rebuild_pending", lambda: False)
    monkeypatch.setattr(maintenance, "maybe_build_market_map", lambda: None)
    monkeypatch.setattr(maintenance, "run_requested_map_build", lambda: False)
    monkeypatch.setattr(maintenance, "_embedding_interval", lambda: 0.0)
    duties = maintenance.MaintenanceDuties(threaded=True)
    try:
        duties.tick()
        duties.tick()
        assert started == ["embeddings"]
    finally:
        release.set()


def test_api_leader_skips_maintenance_when_worker_runs_it(monkeypatch):
    import backend.main as main
    from backend.settings import get_settings

    monkeypatch.setenv("MAINTENANCE_BACKEND", "worker")
    get_settings.cache_clear()
    try:
        assert main._LeaderDuties()._maintenance is None
        monkeypatch.setenv("MAINTENANCE_BACKEND", "inprocess")
        get_settings.cache_clear()
        assert main._LeaderDuties()._maintenance is not None
    finally:
        get_settings.cache_clear()


# --- процесс jobdesk-maintenance-worker -------------------------------------------------------


class _Duties:
    def __init__(self, stop: threading.Event, ticks_before_stop: int = 1) -> None:
        self.ticks = 0
        self._stop = stop
        self._left = ticks_before_stop

    def tick(self) -> None:
        self.ticks += 1
        self._left -= 1
        if self._left <= 0:
            self._stop.set()


def test_worker_loop_runs_duties_only_with_lock(pg_db):
    """Две реплики: задачи выполняет та, что держит lock; heartbeat бьёт у обеих."""
    holder = LeaderLock(maintenance_worker.MAINTENANCE_LOCK_KEY)
    assert holder.try_acquire()
    try:
        stop = threading.Event()
        duties = _Duties(stop)
        beats: list[int] = []

        def beat() -> None:
            beats.append(1)
            if len(beats) >= 4:
                stop.set()

        maintenance_worker.run_loop(
            stop, lock=LeaderLock(maintenance_worker.MAINTENANCE_LOCK_KEY), duties=duties, beat=beat, tick_seconds=0
        )
        assert duties.ticks == 0 and beats
    finally:
        holder.release()

    stop = threading.Event()
    duties = _Duties(stop)
    lock = LeaderLock(maintenance_worker.MAINTENANCE_LOCK_KEY)
    maintenance_worker.run_loop(stop, lock=lock, duties=duties, beat=lambda: None, tick_seconds=0)
    assert duties.ticks == 1
    # Цикл завершился — lock снят, роль может взять другая реплика.
    other = LeaderLock(maintenance_worker.MAINTENANCE_LOCK_KEY)
    try:
        assert other.try_acquire()
    finally:
        other.release()


def test_worker_loop_survives_failing_tick(pg_db):
    stop = threading.Event()
    ticks: list[int] = []

    class Failing:
        def tick(self) -> None:
            ticks.append(1)
            if len(ticks) >= 2:
                stop.set()
            raise RuntimeError("boom")

    maintenance_worker.run_loop(
        stop,
        lock=LeaderLock(maintenance_worker.MAINTENANCE_LOCK_KEY),
        duties=Failing(),
        beat=lambda: None,
        tick_seconds=0,
    )
    assert len(ticks) == 2


def test_requests_keep_latest_time_and_complete_only_handled(pg_db):
    task = maintenance_requests.TASK_MARKET_MAP
    with session_scope() as s:
        first = maintenance_requests.request_task(s, task)
    with session_scope() as s:
        assert maintenance_requests.pending_since(s, task) == first
        second = maintenance_requests.request_task(s, task)
    with session_scope() as s:
        maintenance_requests.complete_task(s, task, requested_at=first)
        assert maintenance_requests.pending_since(s, task) == second  # новее выполненного — остаётся
    with session_scope() as s:
        maintenance_requests.complete_task(s, task, requested_at=second)
        assert maintenance_requests.pending_since(s, task) is None
