"""Heartbeat воркера, Docker healthcheck по нему и watchdog зависшего процесса."""

from __future__ import annotations

import threading
import time

from backend.workers import healthcheck, liveness


def test_healthcheck_reads_fresh_heartbeat_from_metrics():
    now = 1_700_000_000.0
    text = f"# HELP x\nworker_heartbeat_timestamp_seconds {now - 30:.6e}\nother_metric 1\n"

    ok, reason = healthcheck.check(text, max_age=180, now=now)

    assert ok is True, reason


def test_healthcheck_fails_on_stale_or_missing_heartbeat():
    now = 1_700_000_000.0
    stale = f"worker_heartbeat_timestamp_seconds {now - 600}\n"

    assert healthcheck.check(stale, max_age=180, now=now)[0] is False
    assert healthcheck.check("other_metric 1\n", max_age=180, now=now)[0] is False
    # Gauge до первого beat — 0.0: «нет heartbeat», а не «здоров».
    assert healthcheck.check("worker_heartbeat_timestamp_seconds 0.0\n", max_age=180, now=now)[0] is False


def test_beat_updates_exported_gauge():
    from tests.conftest import metrics_text

    liveness.beat()

    ts = healthcheck.heartbeat_from_metrics(metrics_text())
    assert ts is not None and abs(time.time() - ts) < 5


def test_rq_heartbeat_beats_liveness(monkeypatch):
    calls = []
    monkeypatch.setattr(liveness.SimpleWorker, "heartbeat", lambda self, *a, **k: calls.append(a))
    monkeypatch.setattr(liveness, "_last_beat", 0.0)

    liveness.LivenessWorker.heartbeat(object.__new__(liveness.LivenessWorker), 120)

    assert calls == [(120,)]
    assert liveness.seconds_since_beat() < 5


def test_watchdog_exits_when_heartbeat_is_stale(monkeypatch):
    exited = threading.Event()
    codes: list[int] = []

    def fake_exit(code: int) -> None:
        codes.append(code)
        exited.set()

    monkeypatch.setattr(liveness, "beat", lambda: None)  # start_watchdog не освежает heartbeat
    monkeypatch.setattr(liveness, "_last_beat", time.time() - 10)
    stop = liveness.start_watchdog(1.0, name="test-worker", exit_fn=fake_exit)
    try:
        assert exited.wait(timeout=5)
    finally:
        stop.set()
    assert codes == [liveness.STALL_EXIT_CODE]


def test_watchdog_stays_quiet_while_beating(monkeypatch):
    codes: list[int] = []
    stop = liveness.start_watchdog(2.0, name="test-worker", exit_fn=codes.append)
    try:
        for _ in range(6):
            liveness.beat()
            time.sleep(0.5)
    finally:
        stop.set()
    assert codes == []


def test_watchdog_disabled_with_zero():
    assert liveness.start_watchdog(0, name="test-worker", exit_fn=lambda code: None) is None
