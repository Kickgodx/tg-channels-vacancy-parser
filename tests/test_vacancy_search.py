"""Полнотекстовый поиск каталога: ``search_tsv`` и окно ранжирования (catalog/vacancy-search)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from tests.conftest import new_parse_run

from backend.db import vacancy_repository
from backend.db.repository import upsert_vacancies
from backend.db.session import get_session_factory
from backend.schemas import VacancyQuery
from jobdesk.models.vacancy import Vacancy
from jobdesk.utils.hashing import vacancy_content_id

_BASE = datetime(2026, 10, 1, tzinfo=UTC)


def _seed(texts_by_age: list[str]) -> list[str]:
    """Вакансии с текстами; первая — самая новая. Возвращает id в том же порядке."""
    vacancies = [
        Vacancy(
            id=vacancy_content_id(text),
            source_post_key=f"chan-{i}",
            source_channel="chan",
            source_url="https://t.me/s/chan",
            post_date=_BASE - timedelta(days=i),
            raw_text=text,
            cleaned_text=text,
        )
        for i, text in enumerate(texts_by_age)
    ]
    session = get_session_factory()()
    try:
        upsert_vacancies(session, vacancies, parse_run_id=new_parse_run(session))
        session.commit()
    finally:
        session.close()
    return [v.id for v in vacancies]


def _search(client: TestClient, q: str) -> dict:
    r = client.get("/api/v1/vacancies", params={"q": q, "page_size": 50})
    assert r.status_code == 200, r.text
    return r.json()


def test_search_matches_other_word_form(admin_client: TestClient):
    dev, _designer = _seed(["Ищем разработчиков в команду", "Нужен дизайнер интерфейсов"])

    body = _search(admin_client, "разработчик")

    assert [i["id"] for i in body["items"]] == [dev]
    assert body["total"] == 1


def test_relevance_orders_matches_inside_window(admin_client: TestClient):
    newer, older = _seed(
        [
            "Python в стеке, остальное обсудим",
            "Python, Python и ещё раз Python: Python-разработчик на Python",
        ]
    )

    body = _search(admin_client, "python")

    assert [i["id"] for i in body["items"]] == [older, newer]


def test_match_outside_window_goes_after_it(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vacancy_repository, "FTS_RANK_WINDOW", 3)
    ids = _seed(
        [
            "Go в стеке",
            "Go и Kubernetes",
            "Go, немного Python",
            "Go Go Go Go Go: Go-разработчик, Go и только Go",  # самая старая и самая релевантная
        ]
    )

    body = _search(admin_client, "go")

    assert body["total"] == 4
    assert body["items"][-1]["id"] == ids[3]
    assert {i["id"] for i in body["items"][:3]} == set(ids[:3])


def test_export_uses_same_search_order_as_list(admin_client: TestClient):
    _seed(
        [
            "Python в стеке",
            "Python, Python и Python",
            "Python-разработчик, Python",
        ]
    )
    listed = [i["id"] for i in _search(admin_client, "python")["items"]]

    session = get_session_factory()()
    try:
        query = VacancyQuery(q="python", sort_by="post_date", sort_order="desc")
        exported = [v.id for v in vacancy_repository.iter_export_vacancies(session, query, limit=100)]
    finally:
        session.close()

    assert exported == listed


def _seed_salaries(salaries: list[float | None]) -> list[str]:
    vacancies = [
        Vacancy(
            id=vacancy_content_id(f"Вакансия {i}"),
            source_post_key=f"sal-{i}",
            source_channel="chan",
            source_url="https://t.me/s/chan",
            post_date=_BASE - timedelta(days=i),
            raw_text=f"Вакансия {i}",
            cleaned_text=f"Вакансия {i}",
            detected_salary_avg=s,
            detected_salary_avg_rub=s,
            salary_currency="RUB" if s is not None else None,
        )
        for i, s in enumerate(salaries)
    ]
    session = get_session_factory()()
    try:
        upsert_vacancies(session, vacancies, parse_run_id=new_parse_run(session))
        session.commit()
    finally:
        session.close()
    return [v.id for v in vacancies]


def _ids(client: TestClient, **params) -> list[str]:
    r = client.get("/api/v1/vacancies", params=params)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def test_salary_sort_keeps_nulls_last_both_directions(admin_client: TestClient):
    """Сортировка мимо индекса по дате идёт двухфазной выборкой страницы — порядок прежний."""
    top, empty, low = _seed_salaries([300_000.0, None, 150_000.0])

    assert _ids(admin_client, sort_by="detected_salary_avg_rub") == [top, low, empty]
    assert _ids(admin_client, sort_by="detected_salary_avg_rub", sort_order="asc") == [low, top, empty]


def test_pages_of_full_sort_do_not_overlap(admin_client: TestClient):
    ids = _seed_salaries([100.0, 500.0, None, 300.0, 200.0])
    expected = [ids[0], ids[4], ids[3], ids[1], ids[2]]  # asc по зарплате, пусто — в конце

    pages = [
        _ids(admin_client, sort_by="detected_salary_avg_rub", sort_order="asc", page=p, page_size=2) for p in (1, 2, 3)
    ]
    assert [vid for page in pages for vid in page] == expected
    r = admin_client.get("/api/v1/vacancies", params={"sort_by": "detected_salary_avg_rub", "page": 2, "page_size": 2})
    assert r.json()["total"] == 5


def test_search_pages_follow_rank_order(admin_client: TestClient):
    _seed(["Python", "Python Python", "Python Python Python", "Python Python Python Python"])
    full = _ids(admin_client, q="python", page_size=50)
    paged = [vid for p in (1, 2) for vid in _ids(admin_client, q="python", page=p, page_size=2)]
    assert paged == full
