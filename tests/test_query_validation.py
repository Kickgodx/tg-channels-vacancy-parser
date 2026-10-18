"""Невалидные query-параметры → 422 с описанием поля, а не 500 из обработчика."""

from __future__ import annotations

import pytest

from backend.schemas import IMPORT_TEXT_MAX_LENGTH, RESUME_TEXT_MAX_LENGTH


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/vacancies?page=0",
        "/api/v1/vacancies?page_size=1000",
        "/api/v1/vacancies?page=abc",
        "/api/v1/vacancies?date_from=abc",
        "/api/v1/vacancies?date_to=2026-13-01",
        "/api/v1/vacancies?sort_by=raw_text",
        "/api/v1/vacancies?q=" + "x" * 501,
        "/api/v1/vacancies/metrics?date_from=yesterday",
        "/api/v1/vacancies/export.xlsx?date_to=bad",
        "/api/v1/analytics/salaries?date_from=bad",
        "/api/v1/analytics/sources?has_salary=maybe",
        "/api/v1/analytics/companies?limit=0",
        "/api/v1/runs?limit=100000",
        "/api/v1/runs?offset=-1",
    ],
)
def test_invalid_query_is_422(admin_client, url):
    r = admin_client.get(url)
    assert r.status_code == 422, (url, r.status_code, r.text)
    assert r.json()["detail"]["code"] == "VALIDATION"


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/vacancies?page=2&page_size=500&date_from=2026-01-01&date_to=2026-12-31",
        "/api/v1/vacancies?date_from=&date_to=",  # пустой параметр — «не задан»
        "/api/v1/vacancies?specialization=Python&specialization=QA&has_salary=true",
        "/api/v1/vacancies/metrics?date_from=2026-01-01",
        "/api/v1/analytics/salaries?specialization=Python",
        "/api/v1/analytics/companies?limit=100",
        "/api/v1/runs?limit=200&offset=0",
    ],
)
def test_valid_query_passes(admin_client, url):
    assert admin_client.get(url).status_code == 200, url


def test_run_vacancies_page_bounds(admin_client):
    from backend.db import parse_run_repository as pr
    from backend.db.session import session_scope

    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="completed").id
    assert admin_client.get(f"/api/v1/runs/{rid}/vacancies?page_size=501").status_code == 422
    assert admin_client.get(f"/api/v1/runs/{rid}/vacancies?page_size=500").status_code == 200


def test_body_text_limits(admin_client):
    too_long_resume = admin_client.post(
        "/api/v1/resumes", json={"name": "CV", "text": "x" * (RESUME_TEXT_MAX_LENGTH + 1)}
    )
    assert too_long_resume.status_code == 422
    too_long_import = admin_client.post(
        "/api/v1/vacancies/import", json={"texts": ["x" * (IMPORT_TEXT_MAX_LENGTH + 1)]}
    )
    assert too_long_import.status_code == 422


def test_match_score_sort_requires_resume(admin_client):
    from backend.db import parse_run_repository as pr
    from backend.db.session import session_scope

    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="completed").id
    for url in (
        "/api/v1/vacancies?sort_by=match_score",
        "/api/v1/vacancies/export.xlsx?sort_by=match_score",
        f"/api/v1/runs/{rid}/vacancies?sort_by=match_score",
    ):
        r = admin_client.get(url)
        assert r.status_code == 422, (url, r.text)
        assert r.json()["detail"]["code"] == "VALIDATION"
        assert "resume_id" in r.json()["detail"]["message"]

    resume_id = admin_client.post("/api/v1/resumes", json={"name": "CV", "text": "python"}).json()["id"]
    assert admin_client.get(f"/api/v1/vacancies?sort_by=match_score&resume_id={resume_id}").status_code == 200


def test_whitespace_only_names_are_rejected(admin_client):
    assert admin_client.post("/api/v1/filters", json={"name": "   ", "params": {}}).status_code == 422
    assert admin_client.get("/api/v1/filters").json()["items"] == []

    assert admin_client.post("/api/v1/resumes", json={"name": "   ", "text": "python"}).status_code == 422
    rid = admin_client.post("/api/v1/resumes", json={"name": "  CV  ", "text": "python"}).json()["id"]
    assert admin_client.put(f"/api/v1/resumes/{rid}", json={"name": "   "}).status_code == 422
    assert admin_client.get(f"/api/v1/resumes/{rid}").json()["name"] == "CV"

    saved = admin_client.post("/api/v1/filters", json={"name": "  Remote  ", "params": {}})
    assert (saved.status_code, saved.json()["name"]) == (201, "Remote")
