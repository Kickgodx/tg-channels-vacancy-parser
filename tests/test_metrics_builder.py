"""Метрики прогона: временные ряды по дате поста."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from jobdesk.analytics.metrics_builder import build_metrics
from jobdesk.models.vacancy import Vacancy


def _vac(i: int, post_date: datetime | None) -> Vacancy:
    text = f"Python backend {i}"
    return Vacancy(
        id=str(i),
        source_post_key=f"ch-{i}",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_url=f"https://t.me/ch/{i}",
        post_date=post_date,
        raw_text=text,
        cleaned_text=text,
        parsed_at=datetime.now(UTC),
    )


def _series(buckets) -> list[tuple[str, int]]:
    return [(b.period, b.count) for b in buckets]


def test_time_series_buckets_by_utc_day_week_month():
    msk = timezone(timedelta(hours=3))
    dates = [
        datetime(2025, 12, 29, 1, 0, tzinfo=msk),  # 28.12 по UTC, воскресенье
        datetime(2025, 12, 31, 23, 0),  # naive — как есть
        datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
        datetime(2026, 1, 4, 12, tzinfo=UTC),  # воскресенье
        datetime(2026, 1, 5, 12, tzinfo=UTC),  # понедельник — новая неделя
        datetime(2026, 3, 31, 12, tzinfo=UTC),
        datetime(2026, 2, 1, 12, tzinfo=UTC),
        None,
    ]
    vs = [_vac(i, d) for i, d in enumerate(dates)]

    ms = build_metrics(vs, vs, [], posts_collected=len(vs))

    assert _series(ms.daily) == [
        ("2025-12-28", 1),
        ("2025-12-31", 1),
        ("2026-01-01", 1),
        ("2026-01-04", 1),
        ("2026-01-05", 1),
        ("2026-02-01", 1),
        ("2026-03-31", 1),
    ]
    assert _series(ms.weekly) == [
        ("2025-12-22/2025-12-28", 1),
        ("2025-12-29/2026-01-04", 3),
        ("2026-01-05/2026-01-11", 1),
        ("2026-01-26/2026-02-01", 1),
        ("2026-03-30/2026-04-05", 1),
    ]
    assert _series(ms.monthly) == [("2025-12", 2), ("2026-01", 3), ("2026-02", 1), ("2026-03", 1)]


def test_time_series_empty_without_dates():
    ms = build_metrics([_vac(0, None)], [_vac(0, None)], [], posts_collected=1)
    assert ms.daily == ms.weekly == ms.monthly == []
