"""Агрегаты дашборда: зарплатные квантили, качество каналов, топ компаний."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db import analytics
from backend.db.base import Base
from backend.db.models import VacancyRow
from backend.schemas import VacancyQuery


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 't.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _row(
    idx: int,
    *,
    channel: str = "chan_a",
    grade: str = "middle",
    salary_rub: float | None = None,
    company: str | None = None,
    work_format: str | None = "remote",
    is_duplicate: bool = False,
    score: int = 10,
    post_date: datetime | None = None,
    archived_at: datetime | None = None,
) -> VacancyRow:
    return VacancyRow(
        id=f"id-{idx}",
        source_post_key=f"{channel}-{idx}",
        source_channel=channel,
        source_url="https://t.me/s/ch",
        post_date=post_date or datetime(2026, 9, 1, tzinfo=UTC),
        raw_text="t",
        cleaned_text="t",
        detected_grade=grade,
        detected_salary_avg_rub=salary_rub,
        work_format=work_format,
        company=company,
        is_duplicate=is_duplicate,
        vacancy_score=score,
        archived_at=archived_at,
    )


def test_salary_analytics_quantiles(db_session):
    # 4 значения middle: 100, 200, 300, 400 → медиана 250, q1 175, q3 325
    for i, sal in enumerate([100_000, 200_000, 300_000, 400_000]):
        db_session.add(_row(i, salary_rub=sal))
    db_session.add(_row(10, grade="senior", salary_rub=500_000))
    db_session.commit()

    res = analytics.salary_analytics(db_session, VacancyQuery())

    by_grade = {r.group: r for r in res.by_grade}
    assert set(by_grade) == {"middle", "senior"}
    mid = by_grade["middle"]
    assert mid.count == 4
    assert mid.median == 250_000
    assert mid.q1 == 175_000
    assert mid.q3 == 325_000
    assert mid.min == 100_000
    assert mid.max == 400_000

    assert {b.range for b in res.distribution} == {
        "до 100k",
        "100–150k",
        "150–200k",
        "200–250k",
        "250–300k",
        "300–400k",
        "400k+",
    }
    assert sum(b.count for b in res.distribution) == 5
    band = {b.range: b.count for b in res.distribution}
    assert band["100–150k"] == 1
    assert band["200–250k"] == 1
    assert band["300–400k"] == 1
    assert band["400k+"] == 2

    assert len(res.monthly) == 1
    assert res.monthly[0].period == "2026-09"
    assert res.monthly[0].count == 5
    assert res.monthly[0].median == 300_000


def test_source_quality_counts_duplicates(db_session):
    db_session.add_all(
        [
            _row(1, channel="good", salary_rub=200_000, score=20),
            _row(2, channel="good", salary_rub=None, score=10),
            _row(3, channel="good", is_duplicate=True),
            _row(4, channel="spammy", is_duplicate=True),
            _row(5, channel="spammy", is_duplicate=True),
            _row(6, channel="spammy", score=5),
        ]
    )
    db_session.commit()

    res = {s.channel: s for s in analytics.source_quality(db_session, VacancyQuery())}

    assert res["good"].vacancies == 2
    assert res["good"].duplicates == 1
    assert res["good"].unique_share == pytest.approx(2 / 3)
    assert res["good"].avg_score == pytest.approx(15.0)
    assert res["good"].with_salary_share == pytest.approx(0.5)

    assert res["spammy"].vacancies == 1
    assert res["spammy"].duplicates == 2
    assert res["spammy"].unique_share == pytest.approx(1 / 3)


def test_top_companies_ordered_by_count(db_session):
    db_session.add_all(
        [
            _row(1, company="Beta", salary_rub=150_000),
            _row(2, company="Alpha"),
            _row(3, company="Alpha", salary_rub=300_000),
            _row(4, company="Alpha"),
            _row(5, company=None),
        ]
    )
    db_session.commit()

    res = analytics.top_companies(db_session, VacancyQuery())

    assert [c.company for c in res] == ["Alpha", "Beta"]
    assert res[0].vacancies == 3
    assert res[0].with_salary == 1
    assert res[0].avg_salary_rub == 300_000
    assert res[1].vacancies == 1


def test_analytics_respects_filters(db_session):
    db_session.add_all(
        [
            _row(1, channel="a", salary_rub=100_000),
            _row(2, channel="b", salary_rub=900_000),
        ]
    )
    db_session.commit()

    res = analytics.salary_analytics(db_session, VacancyQuery(source_channel="a"))
    assert sum(r.count for r in res.by_grade) == 1
    assert res.by_grade[0].median == 100_000


def test_salary_analytics_yearly_and_period_groups(db_session):
    db_session.add_all(
        [
            _row(
                1, grade="junior", salary_rub=100_000, work_format="remote", post_date=datetime(2024, 3, 1, tzinfo=UTC)
            ),
            _row(
                2, grade="junior", salary_rub=200_000, work_format="office", post_date=datetime(2024, 8, 1, tzinfo=UTC)
            ),
            _row(
                3, grade="middle", salary_rub=300_000, work_format="remote", post_date=datetime(2025, 1, 15, tzinfo=UTC)
            ),
            _row(
                4, grade="middle", salary_rub=400_000, work_format="remote", post_date=datetime(2025, 6, 1, tzinfo=UTC)
            ),
        ]
    )
    db_session.commit()

    res = analytics.salary_analytics(db_session, VacancyQuery())

    yearly = {b.period: b for b in res.yearly}
    assert set(yearly) == {"2024", "2025"}
    assert yearly["2024"].count == 2
    assert yearly["2024"].median == 150_000
    assert yearly["2025"].count == 2
    assert yearly["2025"].median == 350_000

    grade_y = {(b.period, b.group): b for b in res.by_grade_yearly}
    assert grade_y[("2024", "junior")].count == 2
    assert grade_y[("2024", "junior")].median == 150_000
    assert grade_y[("2025", "middle")].count == 2

    wf_y = {(b.period, b.group): b for b in res.by_work_format_yearly}
    assert wf_y[("2024", "remote")].count == 1
    assert wf_y[("2024", "office")].count == 1

    # Drill-down: один год → monthly + by_*_monthly
    y2025 = analytics.salary_analytics(
        db_session,
        VacancyQuery(date_from=datetime(2025, 1, 1, tzinfo=UTC), date_to=datetime(2025, 12, 31, tzinfo=UTC)),
    )
    assert [b.period for b in y2025.monthly] == ["2025-01", "2025-06"]
    assert {(b.period, b.group) for b in y2025.by_grade_monthly} == {("2025-01", "middle"), ("2025-06", "middle")}
    assert all(b.period.startswith("2025") for b in y2025.by_work_format_monthly)


def test_salary_analytics_include_archived_active_and_archived(db_session):
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _row(1, channel="hh", salary_rub=100_000, post_date=datetime(2024, 5, 1, tzinfo=UTC)),
            _row(
                2,
                channel="hh",
                salary_rub=300_000,
                post_date=datetime(2024, 6, 1, tzinfo=UTC),
                archived_at=now,
            ),
        ]
    )
    db_session.commit()

    active_only = analytics.salary_analytics(db_session, VacancyQuery(source_channel="hh"))
    assert sum(b.count for b in active_only.yearly) == 1

    both = analytics.salary_analytics(
        db_session,
        VacancyQuery(source_channel="hh", include_archived=True),
    )
    assert sum(b.count for b in both.yearly) == 2
    assert both.yearly[0].median == 200_000
