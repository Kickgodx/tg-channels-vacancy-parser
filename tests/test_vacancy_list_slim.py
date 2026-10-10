"""Список вакансий отдаёт preview без полных текстов; деталь — полный объект."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.db.repository import upsert_vacancies
from backend.db.session import get_session_factory
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id

_LONG = ("Python senior remote " * 40).strip()  # >> 120 символов


def _seed_long_vacancy() -> str:
    vid = vacancy_content_id(_LONG)
    v = Vacancy(
        id=vid,
        source_post_key="chan-99",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/99",
        post_date=datetime(2026, 3, 1, tzinfo=UTC),
        raw_text=_LONG,
        cleaned_text=_LONG,
        detected_specializations=["Python"],
        detected_grade="senior",
        work_format="remote",
    )
    factory = get_session_factory()
    session = factory()
    try:
        upsert_vacancies(session, [v], parse_run_id=uuid.uuid4())
        session.commit()
    finally:
        session.close()
    return vid


def test_list_vacancies_returns_preview_without_full_texts(api_client: TestClient):
    vid = _seed_long_vacancy()
    r = api_client.get("/api/v1/vacancies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["id"] == vid
    assert "raw_text" not in item
    assert "cleaned_text" not in item
    assert "preview" in item
    assert len(item["preview"]) <= 121  # 120 + возможное «…»
    assert item["preview"].startswith("Python senior remote")
    assert item["preview"].endswith("…")


def test_get_vacancy_returns_full_texts(api_client: TestClient):
    vid = _seed_long_vacancy()
    r = api_client.get(f"/api/v1/vacancies/{vid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == vid
    assert body["cleaned_text"] == _LONG
    assert body["raw_text"] == _LONG


def test_get_vacancy_not_found(api_client: TestClient):
    r = api_client.get("/api/v1/vacancies/missing-id-xyz")
    assert r.status_code == 404
