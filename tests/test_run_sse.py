from __future__ import annotations

from datetime import UTC, datetime

from backend.db.models import ParseRunRow
from backend.db.session import get_session_factory


def test_run_events_terminal_emits_done_immediately(api_client):
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

    with api_client.stream("GET", f"/api/v1/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")

    assert "event: done" in body
