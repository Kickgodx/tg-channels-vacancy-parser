"""Профиль компании, флаги вакансии и нормализованный ключ компании."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.db import analytics
from backend.db.company_profile import (
    FREQUENT_ROLE_MIN,
    backfill_company_keys,
    company_profile,
    vacancy_flags,
)
from backend.db.models import VacancyRow, VacancySightingRow
from backend.db.vacancy_mapper import vacancy_to_row
from backend.schemas import VacancyQuery
from jobdesk.models.vacancy import Vacancy
from jobdesk.utils.company import company_key

_ids = iter(range(1, 1_000_000))
_DAY = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("ООО «Ромашка»", "ромашка"),
        ("РОМАШКА LLC", "ромашка"),
        ("Dr. Web", "dr web"),
        ("Яндекс Крауд", "яндекс крауд"),
        ("ООО", None),
        ("", None),
        (None, None),
        ("Ёлка", "елка"),
    ],
)
def test_company_key(name, key):
    assert company_key(name) == key


def _row(
    company: str | None = "Ромашка",
    *,
    roles: list[str] | None = None,
    salary: float | None = None,
    post_date: datetime | None = None,
    archived: bool = False,
    duplicate: bool = False,
    channel: str = "ch",
) -> VacancyRow:
    i = next(_ids)
    return VacancyRow(
        id=f"c-{i}",
        source_post_key=f"{channel}-{i}",
        source_channel=channel,
        source_url="https://t.me/s/ch",
        post_date=post_date or _DAY,
        raw_text="t",
        cleaned_text="t",
        company=company,
        company_key=company_key(company),
        detected_specializations=roles if roles is not None else ["QA"],
        technologies=["Python"],
        detected_grade="middle",
        detected_salary_avg_rub=salary,
        archived_at=_DAY if archived else None,
        is_duplicate=duplicate,
    )


def _sighting(vacancy: VacancyRow, post_date: datetime, n: int) -> VacancySightingRow:
    return VacancySightingRow(
        source_post_key=f"s-{vacancy.id}-{n}",
        vacancy_id=vacancy.id,
        source_channel=vacancy.source_channel,
        post_date=post_date,
        is_duplicate=n > 0,
    )


def test_mapper_writes_company_key():
    v = Vacancy(
        id="x",
        source_post_key="k",
        source_channel="ch",
        source_url="u",
        raw_text="t",
        cleaned_text="t",
        company="ООО Ромашка",
    )
    assert vacancy_to_row(v).company_key == "ромашка"


def test_profile_merges_spellings(db_session):
    db_session.add_all(
        [
            _row("Ромашка", salary=100_000),
            _row("Ромашка"),
            _row("РОМАШКА", salary=200_000, archived=True),
            _row("ООО Ромашка", roles=["Backend"], salary=300_000),
            _row("Ромашка", duplicate=True),
            _row("Ромашка Крауд"),
        ]
    )
    db_session.commit()

    p = company_profile(db_session, "ромашка ооо")
    assert p is not None
    assert p.key == "ромашка"
    assert p.name == "Ромашка"
    assert set(p.variants) == {"Ромашка", "РОМАШКА", "ООО Ромашка"}
    assert (p.vacancies, p.active, p.with_salary) == (4, 3, 3)
    assert p.salary_median == 200_000
    assert [r.name for r in p.roles] == ["QA", "Backend"]
    assert len(p.recent) == 4

    assert company_profile(db_session, "Нет такой") is None


def test_flags_repost_and_long_open(db_session):
    v = _row()
    db_session.add(v)
    db_session.flush()
    db_session.add_all(
        [
            _sighting(v, _DAY, 0),
            _sighting(v, _DAY + timedelta(hours=1), 1),  # тот же день — не считается
            _sighting(v, _DAY + timedelta(days=10), 2),
            _sighting(v, _DAY + timedelta(days=21), 3),
        ]
    )
    db_session.commit()

    flags = {f.code: f for f in vacancy_flags(db_session, v).flags}
    assert flags["reposted"].value == 3
    assert flags["reposted"].message == "Тот же текст публиковали в 3 разных дня"
    assert flags["long_open"].value == 21
    assert flags["no_salary"].severity == "info"


def test_no_flags_for_single_post_with_salary(db_session):
    v = _row(salary=200_000)
    db_session.add(v)
    db_session.flush()
    db_session.add(_sighting(v, _DAY, 0))
    db_session.commit()
    assert vacancy_flags(db_session, v).flags == []


def test_frequent_role_within_window(db_session):
    target = _row(post_date=_DAY, salary=1)
    same_role = [_row("ромашка", post_date=_DAY - timedelta(days=d)) for d in range(1, FREQUENT_ROLE_MIN)]
    noise = [
        _row(post_date=_DAY - timedelta(days=200)),  # вне окна
        _row(roles=["Backend"]),  # другая роль
        _row("Другая"),  # другая компания
    ]
    db_session.add_all([target, *same_role, *noise])
    db_session.commit()

    flags = {f.code: f for f in vacancy_flags(db_session, target).flags}
    assert flags["frequent_role"].value == FREQUENT_ROLE_MIN
    assert "5 вакансий QA" in flags["frequent_role"].message

    db_session.delete(same_role[0])
    db_session.commit()
    assert "frequent_role" not in {f.code for f in vacancy_flags(db_session, target).flags}


def test_backfill_company_keys(db_session):
    row = _row("ООО Ромашка")
    row.company_key = None
    db_session.add(row)
    db_session.commit()
    assert backfill_company_keys(db_session) == 1
    db_session.commit()
    db_session.refresh(row)
    assert row.company_key == "ромашка"
    assert backfill_company_keys(db_session) == 0


def test_top_companies_merge_spellings(db_session):
    db_session.add_all([_row("Bell Integrator"), _row("Bell Integrator"), _row("BELL INTEGRATOR"), _row("Alpha")])
    db_session.commit()
    res = analytics.top_companies(db_session, VacancyQuery())
    assert [(c.company, c.vacancies) for c in res] == [("Bell Integrator", 3), ("Alpha", 1)]


def test_endpoints(admin_client, db_session):
    v = _row()
    db_session.add(v)
    db_session.commit()
    r = admin_client.get("/api/v1/companies/profile", params={"name": "ромашка"})
    assert r.status_code == 200
    assert r.json()["vacancies"] == 1
    assert admin_client.get("/api/v1/companies/profile", params={"name": "nope"}).status_code == 404
    flags = admin_client.get(f"/api/v1/vacancies/{v.id}/flags")
    assert flags.status_code == 200
    assert [f["code"] for f in flags.json()["flags"]] == ["no_salary"]
