"""Аналитика технологий: премия с поправкой на грейд, спрос по месяцам, рост и падение."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.db.models import VacancyRow
from backend.db.technology_analytics import (
    MOMENTUM_WINDOW_DAYS,
    Stratum,
    grade_adjusted_premium,
    technology_analytics,
)
from backend.schemas import VacancyQuery

_ids = iter(range(1, 1_000_000))


def _row(
    *,
    techs: list[str],
    grade: str = "middle",
    salary: float | None = None,
    post_date: datetime | None = None,
    archived: bool = False,
) -> VacancyRow:
    i = next(_ids)
    return VacancyRow(
        id=f"t-{i}",
        source_post_key=f"k-{i}",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_date=post_date or datetime(2026, 9, 1, tzinfo=UTC),
        raw_text="t",
        cleaned_text="t",
        detected_grade=grade,
        detected_salary_avg_rub=salary,
        technologies=techs,
        archived_at=datetime(2026, 9, 2, tzinfo=UTC) if archived else None,
    )


def test_premium_weighted_over_comparable_grades():
    strata = [
        Stratum("middle", 6, 300_000, 5, 200_000),  # +50%
        Stratum("senior", 4, 600_000, 5, 300_000),  # мало с технологией — не сравнивается
        Stratum("lead", 6, 400_000, 8, 400_000),  # 0%
    ]
    premium, compared = grade_adjusted_premium(strata)
    assert compared == 12
    assert premium == pytest.approx(0.25)


def test_premium_none_when_too_few():
    premium, compared = grade_adjusted_premium([Stratum("middle", 5, 300_000, 5, 200_000)])
    assert premium is None
    assert compared == 5


def _premium_corpus(db_session):
    # middle: Go у 5 по 300k, без Go 5 по 200k; senior: 5 и 5 по 400k.
    for _ in range(5):
        db_session.add(_row(techs=["Go"], salary=300_000))
        db_session.add(_row(techs=["Python"], salary=200_000))
        db_session.add(_row(techs=["Go"], grade="senior", salary=400_000))
        db_session.add(_row(techs=["Python"], grade="senior", salary=400_000))
    db_session.commit()


def test_premium_from_db_controls_for_grade(db_session):
    _premium_corpus(db_session)
    res = technology_analytics(db_session, VacancyQuery())
    go = {p.technology: p for p in res.premium}["Go"]
    assert go.vacancies == 10
    assert go.compared_vacancies == 10
    assert go.premium == pytest.approx(0.25)
    assert {(g.grade, g.with_count, g.without_count, g.compared) for g in go.by_grade} == {
        ("middle", 5, 5, True),
        ("senior", 5, 5, True),
    }


def test_own_technology_filter_ignored(db_session):
    _premium_corpus(db_session)
    res = technology_analytics(db_session, VacancyQuery(technologies=["Go"]))
    go = {p.technology: p for p in res.premium}["Go"]
    assert go.premium == pytest.approx(0.25)


def test_trend_shares_by_month(db_session):
    for _ in range(3):
        db_session.add(_row(techs=["Go"], post_date=datetime(2026, 8, 10, tzinfo=UTC)))
    db_session.add(_row(techs=["Java"], post_date=datetime(2026, 8, 11, tzinfo=UTC)))
    db_session.add(_row(techs=["Go"], post_date=datetime(2026, 9, 5, tzinfo=UTC)))
    db_session.add(_row(techs=["Go"], post_date=datetime(2026, 9, 6, tzinfo=UTC), archived=True))
    db_session.commit()

    trend = technology_analytics(db_session, VacancyQuery()).trend
    assert trend.months == ["2026-08", "2026-09"]
    assert trend.totals == [4, 1]
    series = {s.technology: s.counts for s in trend.series}
    assert series["Go"] == [3, 1]
    assert series["Java"] == [1, 0]

    with_archive = technology_analytics(db_session, VacancyQuery(include_archived=True)).trend
    assert with_archive.totals == [4, 2]


def test_momentum_compares_last_two_windows(db_session):
    anchor = datetime(2026, 10, 20, tzinfo=UTC)
    recent = anchor - timedelta(days=1)
    prev = anchor - timedelta(days=MOMENTUM_WINDOW_DAYS + 1)
    # Последнее окно: 40 вакансий, Go у 20 (50%); предыдущее: 40, Go у 10 (25%).
    for i in range(40):
        db_session.add(_row(techs=["Go"] if i < 20 else ["Java"], post_date=recent))
        db_session.add(_row(techs=["Go"] if i < 10 else ["Java"], post_date=prev))
    db_session.add(_row(techs=["Java"], post_date=anchor))
    db_session.commit()

    res = technology_analytics(db_session, VacancyQuery())
    assert res.momentum_anchor == anchor.date()
    by = {m.technology: m for m in res.momentum}
    assert (by["Go"].recent, by["Go"].previous) == (20, 10)
    assert by["Go"].delta_pp == pytest.approx((20 / 41 - 10 / 40) * 100)
    assert res.momentum[0].technology == "Go"


def test_momentum_empty_without_history(db_session):
    for _ in range(40):
        db_session.add(_row(techs=["Go"], post_date=datetime(2026, 10, 1, tzinfo=UTC)))
    db_session.commit()
    res = technology_analytics(db_session, VacancyQuery())
    assert res.momentum == []


def test_endpoint(admin_client):
    r = admin_client.get("/api/v1/analytics/technologies")
    assert r.status_code == 200
    body = r.json()
    assert body["premium"] == []
    assert body["momentum_window_days"] == MOMENTUM_WINDOW_DAYS
