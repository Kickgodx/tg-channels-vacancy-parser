"""Рыночная вилка вакансии: лестница критериев, окно в год, позиция зарплаты вакансии."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.db.market_salary import MIN_COMPARABLE, market_salary
from backend.db.models import VacancyRow

_ids = iter(range(1, 1_000_000))
_DAY = datetime(2026, 9, 1, tzinfo=UTC)


def _row(
    *,
    grade: str = "middle",
    roles: list[str] | None = None,
    techs: list[str] | None = None,
    salary: float | None = None,
    post_date: datetime | None = None,
    duplicate: bool = False,
    archived: bool = False,
) -> VacancyRow:
    i = next(_ids)
    return VacancyRow(
        id=f"m-{i}",
        source_post_key=f"k-{i}",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_date=post_date or _DAY,
        raw_text="t",
        cleaned_text="t",
        detected_grade=grade,
        detected_specializations=roles if roles is not None else ["QA"],
        technologies=techs if techs is not None else ["Python"],
        detected_salary_avg_rub=salary,
        is_duplicate=duplicate,
        archived_at=_DAY if archived else None,
    )


def _add(db, rows):
    db.add_all(rows)
    db.commit()
    return rows


def test_strictest_level_with_enough_comparables(db_session):
    target = _row(salary=None)
    peers = [_row(salary=100_000 + 10_000 * i) for i in range(MIN_COMPARABLE)]
    _add(db_session, [target, *peers])

    m = market_salary(db_session, target)
    assert m.level == "grade_role_tech"
    assert m.count == MIN_COMPARABLE
    assert (m.grade, m.specializations, m.technologies) == ("middle", ["QA"], ["Python"])
    assert m.median == 135_000
    assert m.percentile is None


def test_falls_back_when_too_few(db_session):
    target = _row(techs=["Go"])
    # С Go — только 2, с ролью и грейдом — 8: ступень grade_role.
    peers = [_row(techs=["Go"], salary=300_000) for _ in range(2)]
    peers += [_row(techs=["Java"], salary=200_000) for _ in range(MIN_COMPARABLE - 2)]
    _add(db_session, [target, *peers])

    m = market_salary(db_session, target)
    assert m.level == "grade_role"
    assert m.count == MIN_COMPARABLE
    assert m.technologies == []


def test_unknown_grade_skips_grade_levels(db_session):
    target = _row(grade="unknown")
    peers = [_row(grade=g, salary=150_000) for g in ["junior", "middle", "senior", "lead"] * 2]
    _add(db_session, [target, *peers])

    m = market_salary(db_session, target)
    assert m.level == "role"
    assert m.grade is None
    assert m.count == 8


def test_excludes_duplicates_old_and_self_but_keeps_archived(db_session):
    target = _row(salary=200_000)
    keep = [_row(salary=100_000, archived=True) for _ in range(MIN_COMPARABLE - 1)]
    noise = [
        _row(salary=1, duplicate=True),
        _row(salary=1, post_date=_DAY - timedelta(days=400)),
        _row(salary=None),
    ]
    _add(db_session, [target, *keep, *noise])

    assert market_salary(db_session, target).level is None  # 7 похожих — мало

    _add(db_session, [_row(salary=300_000)])
    m = market_salary(db_session, target)
    assert m.count == MIN_COMPARABLE
    assert m.vacancy_salary == 200_000
    assert m.percentile == 7 / 8


def test_endpoint(admin_client, db_session):
    target = _row()
    _add(db_session, [target])
    r = admin_client.get(f"/api/v1/vacancies/{target.id}/market-salary")
    assert r.status_code == 200
    assert r.json()["level"] is None
    assert admin_client.get("/api/v1/vacancies/nope/market-salary").status_code == 404
