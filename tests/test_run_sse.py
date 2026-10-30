from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import fakeredis
import fakeredis.aioredis

from backend.db import parse_run_repository as pr
from backend.db.models import ParseRunRow
from backend.db.session import get_session_factory, session_scope
from backend.services.parse_run_events import publish_parse_run_event
from backend.services.vacancy_preview import build_vacancies_preview
from jobdesk.models.vacancy import Vacancy


def _vac(vid: str, *, channel: str = "ch", post_id: str = "1") -> Vacancy:
    return Vacancy(
        id=vid,
        source_post_key=f"{channel}-{post_id}",
        source_channel=channel,
        source_url=f"https://t.me/s/{channel}",
        post_url=f"https://t.me/{channel}/{post_id}",
        post_date=datetime.now(UTC),
        raw_text=f"text {vid} {post_id}",
        cleaned_text=f"text {vid} {post_id}",
        detected_specializations=["Python"],
        vacancy_score=5,
        vacancy_substantive_score=3,
    )


def test_get_run_returns_patched_progress(admin_client):
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="running").id
    snap = {
        "posts_processed": 7,
        "posts_total": 20,
        "vacancies_found": 2,
        "phase": "processing",
    }
    with session_scope() as session:
        pr.patch_run_progress(session, rid, snap)

    r = admin_client.get(f"/api/v1/runs/{rid}")
    assert r.status_code == 200
    body = r.json()
    assert body["progress"] is not None
    assert body["progress"]["posts_processed"] == 7
    assert body["progress_updated_at"] is not None


def test_run_events_terminal_emits_done_immediately(admin_client):
    factory = get_session_factory()
    session = factory()
    try:
        row = ParseRunRow(
            status="completed",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            posts_fetched=0,
            config_snapshot={},
        )
        session.add(row)
        session.commit()
        run_id = row.id
    finally:
        session.close()

    with admin_client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")

    assert "event: done" in body


def test_run_events_emits_initial_progress_from_db(admin_client, monkeypatch):
    """Initial progress из БД; live-ветка Redis (TestClient буферит до конца стрима)."""
    fake_server = fakeredis.FakeServer()
    server = fakeredis.FakeStrictRedis(server=fake_server)
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    from backend.queue import rq_conn
    from backend.settings import get_settings

    get_settings.cache_clear()
    rq_conn.reset_redis_connection()
    monkeypatch.setattr(rq_conn, "get_redis_connection", lambda force_new=False: server)
    monkeypatch.setattr(rq_conn, "get_async_redis_client", lambda: fakeredis.aioredis.FakeRedis(server=fake_server))

    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="running").id
    with session_scope() as session:
        pr.patch_run_progress(
            session,
            rid,
            {
                "posts_processed": 3,
                "posts_total": 10,
                "vacancies_found": 1,
                "phase": "processing",
            },
        )

    ready = threading.Event()

    def _finish() -> None:
        ready.wait(timeout=5)
        time.sleep(0.2)
        publish_parse_run_event(rid, event="done", payload={})
        # fallback: Redis SSE also exits on terminal DB status
        with session_scope() as session:
            row = pr.get_parse_run(session, rid)
            if row is not None:
                row.status = "completed"
                session.flush()

    threading.Thread(target=_finish, daemon=True).start()

    with admin_client.stream("GET", f"/api/v1/runs/{rid}/events") as resp:
        assert resp.status_code == 200
        ready.set()
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")

    assert "event: progress" in body
    assert '"posts_processed": 3' in body or '"posts_processed":3' in body
    assert "event: done" in body
    rq_conn.reset_redis_connection()
    get_settings.cache_clear()


def test_run_events_db_poll_without_redis(admin_client, monkeypatch):
    """Без REDIS_URL SSE поллит БД: progress при смене progress_updated_at, затем done."""
    monkeypatch.setenv("REDIS_URL", "")
    from backend.settings import get_settings

    get_settings.cache_clear()

    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="running").id

    ready = threading.Event()

    def _patch_then_complete() -> None:
        ready.wait(timeout=5)
        time.sleep(0.2)
        with session_scope() as session:
            pr.patch_run_progress(
                session,
                rid,
                {
                    "posts_processed": 5,
                    "posts_total": 12,
                    "vacancies_found": 2,
                    "phase": "processing",
                },
            )
        time.sleep(1.2)
        with session_scope() as session:
            row = pr.get_parse_run(session, rid)
            if row is not None:
                row.status = "completed"
                session.flush()

    threading.Thread(target=_patch_then_complete, daemon=True).start()

    with admin_client.stream("GET", f"/api/v1/runs/{rid}/events") as resp:
        assert resp.status_code == 200
        ready.set()
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")

    assert "event: progress" in body
    assert '"posts_processed": 5' in body or '"posts_processed":5' in body
    assert "event: done" in body
    get_settings.cache_clear()


def test_vacancies_preview_dedupes_by_id():
    """Live-preview до дедупа может содержать одинаковые content-id — React key ломается."""
    items = [
        _vac("dup", post_id="1"),
        _vac("uniq", post_id="2"),
        _vac("dup", post_id="3"),
    ]
    preview = build_vacancies_preview(items, limit=30)
    ids = [p["id"] for p in preview]
    assert ids == ["dup", "uniq"]
    assert len(ids) == len(set(ids))


def test_run_events_api_key_via_ticket_not_query(admin_client, monkeypatch):
    """При API_KEY: ключ проверяется при выдаче тикета, ``?api_key=`` не принимается."""
    from backend.settings import get_settings

    monkeypatch.setenv("API_KEY", "sse-secret")
    get_settings.cache_clear()
    try:
        with session_scope() as session:
            rid = pr.create_parse_run(session, config_snapshot={}, status="completed").id

        # Bearer без X-API-Key и ключ в query — 401.
        assert admin_client.get(f"/api/v1/runs/{rid}/events").status_code == 401
        assert admin_client.get(f"/api/v1/runs/{rid}/events?api_key=sse-secret").status_code == 401
        assert admin_client.post("/api/v1/auth/sse-ticket").status_code == 401

        r = admin_client.post("/api/v1/auth/sse-ticket", headers={"X-API-Key": "sse-secret"})
        assert r.status_code == 200
        ticket = r.json()["ticket"]
        # С тикетом ключ не нужен: тикет выдан только запросу с X-API-Key.
        with admin_client.stream("GET", f"/api/v1/runs/{rid}/events?ticket={ticket}") as resp:
            assert resp.status_code == 200
            body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")
        assert "event: done" in body
    finally:
        monkeypatch.delenv("API_KEY", raising=False)
        get_settings.cache_clear()
