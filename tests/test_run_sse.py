from __future__ import annotations

from datetime import UTC, datetime

from backend.api.routes.runs import build_vacancies_preview
from backend.db.models import ParseRunRow
from backend.db.session import get_session_factory
from tg_jobs_parser.models.vacancy import Vacancy


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
