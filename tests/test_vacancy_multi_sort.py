"""Двухступенчатая сортировка списка «дата ↔ зарплата ₽» (catalog/vacancy-search)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from tests.conftest import new_parse_run

from backend.db import vacancy_repository as repo
from backend.db.repository import upsert_vacancies
from backend.db.session import get_session_factory
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.schemas import VacancyQuery
from jobdesk.models.vacancy import Vacancy
from jobdesk.utils.hashing import vacancy_content_id

SALARY = "detected_salary_avg_rub"


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def _seed(rows: list[tuple[str, datetime, float | None]]) -> dict[str, str]:
    """(метка, время поста, зарплата ₽) → {метка: id}."""
    vacancies = [
        Vacancy(
            id=vacancy_content_id(f"Вакансия {label}"),
            source_post_key=f"multi-{label}",
            source_channel="chan",
            source_url="https://t.me/s/chan",
            post_date=posted,
            raw_text=f"Вакансия {label} python",
            cleaned_text=f"Вакансия {label} python",
            detected_salary_avg=salary,
            detected_salary_avg_rub=salary,
            salary_currency="RUB" if salary is not None else None,
        )
        for label, posted, salary in rows
    ]
    session = get_session_factory()()
    try:
        upsert_vacancies(session, vacancies, parse_run_id=new_parse_run(session))
        session.commit()
    finally:
        session.close()
    return {label: vacancy_content_id(f"Вакансия {label}") for label, _, _ in rows}


def _labels(client: TestClient, ids: dict[str, str], **params) -> list[str]:
    r = client.get("/api/v1/vacancies", params={"page_size": 50, **params})
    assert r.status_code == 200, r.text
    by_id = {v: k for k, v in ids.items()}
    return [by_id[i["id"]] for i in r.json()["items"]]


@pytest.fixture
def two_days():
    # 20.10: B богаче A, у C зарплаты нет; 19.10: E в 23:30 UTC — ещё 19-е.
    return _seed(
        [
            ("A", _at(20, 10), 100_000.0),
            ("B", _at(20, 8), 300_000.0),
            ("C", _at(20, 12), None),
            ("E", _at(19, 23, 30), 500_000.0),
            ("F", _at(19, 1), 200_000.0),
        ]
    )


def test_date_only_sorts_by_time(admin_client: TestClient, two_days):
    assert _labels(admin_client, two_days) == ["C", "A", "B", "E", "F"]


def test_day_then_salary(admin_client: TestClient, two_days):
    assert _labels(admin_client, two_days, then_by=SALARY) == ["B", "A", "C", "E", "F"]


def test_day_then_salary_ascending_keeps_empty_last(admin_client: TestClient, two_days):
    assert _labels(admin_client, two_days, then_by=SALARY, then_order="asc") == ["A", "B", "C", "F", "E"]


def test_oldest_day_first_then_salary(admin_client: TestClient, two_days):
    assert _labels(admin_client, two_days, sort_order="asc", then_by=SALARY) == ["E", "F", "B", "A", "C"]


def test_salary_ties_go_newest_first_by_default(admin_client: TestClient):
    ids = _seed(
        [
            ("old", _at(10, 9), 150_000.0),
            ("new", _at(12, 9), 150_000.0),
            ("top", _at(1, 9), 400_000.0),
            ("none", _at(15, 9), None),
        ]
    )
    assert _labels(admin_client, ids, sort_by=SALARY) == ["top", "new", "old", "none"]
    assert _labels(admin_client, ids, sort_by=SALARY, then_by="post_date") == ["top", "new", "old", "none"]
    assert _labels(admin_client, ids, sort_by=SALARY, then_by="post_date", then_order="asc") == [
        "top",
        "old",
        "new",
        "none",
    ]


def test_pages_of_day_salary_sort_do_not_overlap(admin_client: TestClient, two_days):
    """Первая страница — одним запросом по индексу, следующие — через id: порядок общий."""
    full = _labels(admin_client, two_days, then_by=SALARY)
    paged = [label for p in (1, 2, 3) for label in _labels(admin_client, two_days, then_by=SALARY, page=p, page_size=2)]
    assert paged == full


def test_day_salary_sort_with_search_and_resume(admin_client: TestClient, two_days):
    assert set(_labels(admin_client, two_days, q="python", then_by=SALARY)) == set(two_days)
    resume_id = admin_client.post("/api/v1/resumes", json={"name": "CV", "text": "python"}).json()["id"]
    assert _labels(admin_client, two_days, then_by=SALARY, resume_id=resume_id) == ["B", "A", "C", "E", "F"]


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "work_format", "then_by": SALARY},
        {"sort_by": "post_date", "then_by": "post_date"},
        {"sort_by": SALARY, "then_by": SALARY},
        {"then_by": "detected_grade"},
        {"then_by": SALARY, "then_order": "sideways"},
    ],
)
def test_invalid_sort_pair_is_422(admin_client: TestClient, params):
    for path in ("/api/v1/vacancies", "/api/v1/vacancies/export.xlsx"):
        r = admin_client.get(path, params=params)
        assert r.status_code == 422, (path, r.text)


def test_saved_filter_keeps_second_sort(admin_client: TestClient):
    r = admin_client.post("/api/v1/filters", json={"name": "свежие и дорогие", "params": {"then_by": SALARY}})
    assert r.status_code in (200, 201), r.text
    assert r.json()["params"]["then_by"] == SALARY
    assert r.json()["params"]["then_order"] == "desc"

    bad = admin_client.post(
        "/api/v1/filters", json={"name": "x", "params": {"sort_by": "work_format", "then_by": SALARY}}
    )
    assert bad.status_code == 422


def test_export_accepts_second_sort(admin_client: TestClient, two_days):
    r = admin_client.get("/api/v1/vacancies/export.xlsx", params={"then_by": SALARY})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    ("query", "index"),
    [
        (VacancyQuery(then_by=SALARY), "ix_vacancies_active_day_salary"),
        (VacancyQuery(sort_by=SALARY), "ix_vacancies_active_salary_date"),
        (VacancyQuery(sort_by=SALARY, then_by="post_date"), "ix_vacancies_active_salary_date"),
    ],
)
def test_indexed_sorts_match_their_index(db_session, two_days, query, index):
    """ORDER BY списка совпадает с выражением индекса целиком — без узла Sort.

    Проверяется SQL, который строит репозиторий, с параметрами ``$n`` в generic-плане (как у
    подготовленного запроса): выражение дня с bind-параметром вместо литерала ``'UTC'``
    или с другим часовым поясом молча вернуло бы полную сортировку.
    """
    assert not repo._needs_page_ids_first(query)
    stmt = (
        apply_vacancy_filters(select(repo.VacancyRow.id), query)
        .order_by(*repo._vacancy_order_by(query), repo.VacancyRow.id.asc())
        .limit(2)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect(paramstyle="numeric_dollar")))
    assert "$1" in sql  # параметры остались параметрами
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(db_session.connection().exec_driver_sql("EXPLAIN (GENERIC_PLAN) " + sql).scalars())
    assert index in plan
    assert "Sort" not in plan
