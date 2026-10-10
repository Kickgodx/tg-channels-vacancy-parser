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
