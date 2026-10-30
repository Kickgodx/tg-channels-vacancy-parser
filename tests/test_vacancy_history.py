"""История вакансии: изменения перепоста с правками относительно первой публикации."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import text
from tests.conftest import new_parse_run

from backend.db import repository as repo
from backend.db.models import VacancySightingRow
from backend.db.session import session_scope
from backend.services.near_duplicate_merge import apply_merges, find_near_duplicates
from jobdesk.config.schema import AppConfig
from jobdesk.deduplication.changes import is_edited_repost, vacancy_changes
from jobdesk.models.vacancy import Vacancy
from jobdesk.utils.hashing import vacancy_content_id

_POST = (
    "Компания Acme ищет QA инженера в команду платежей. Офис в Москве. Требования: "
    "pytest, API, SQL, Docker, опыт от 3 лет. Задачи: автотесты, ревью, CI. Зарплата 200 000 ₽. "
    "Пишите в телеграм @acme_hr, ответим за день."
)
# Зарплата и стек — правки текста (сходство ~97%); формат меняется только в разборе.
_EDITED = _POST.replace("200 000", "240 000").replace("Docker", "Kafka")
_NOW = datetime.now(UTC).replace(microsecond=0)


def _fields(text, **kw):
    base = dict(
        cleaned_text=text,
        detected_salary_min=None,
        detected_salary_max=None,
        salary_currency=None,
        work_format=None,
        city=None,
        technologies=[],
    )
    return SimpleNamespace(**(base | kw))


def test_changes_salary_format_city_and_mentioned_technologies():
    old = _fields(
        _POST,
        detected_salary_min=200_000.0,
        salary_currency="RUB",
        work_format="office",
        city="Москва",
        technologies=["Python", "Docker", "SQL"],
    )
    new = _fields(
        _EDITED,
        detected_salary_min=240_000.0,
        salary_currency="RUB",
        work_format="remote",
        city=None,
        technologies=["Kafka", "SQL", "Pytest", "Go"],  # Go в тексте нет — шум списка LLM
    )
    changes = {c["field"]: c for c in vacancy_changes(old, new)}
    assert changes["salary"]["old"] == {"min": 200_000.0, "max": None, "currency": "RUB"}
    assert changes["salary"]["new"]["min"] == 240_000.0
    assert (changes["work_format"]["old"], changes["work_format"]["new"]) == ("office", "remote")
    assert "city" not in changes  # пропавший город — не изменение
    # Pytest и Python есть в обоих текстах («pytest» содержит python? нет — целые слова), Go нет в тексте.
    assert changes["technologies"] == {"field": "technologies", "added": ["Kafka"], "removed": ["Docker"]}


def test_no_changes_and_vanished_salary():
    a = _fields(_POST, detected_salary_min=200_000.0, salary_currency="RUB")
    assert vacancy_changes(a, _fields(_POST)) == []  # зарплату агрегатор не перепечатал
    assert vacancy_changes(a, a) == []
    assert is_edited_repost("fuzzy_text:97") and is_edited_repost("fuzzy:90")
    assert not is_edited_repost("exact_raw_hash") and not is_edited_repost(None)


def _v(text: str, *, post: str, days_ago: int, **kw) -> Vacancy:
    kw.setdefault("detected_specializations", ["QA"])
    kw.setdefault("company", "Acme")
    return Vacancy(
        id=vacancy_content_id(text),
        source_post_key=f"qa_jobs-{post}",
        source_channel="qa_jobs",
        source_url="https://t.me/s/qa_jobs",
        post_url=f"https://t.me/qa_jobs/{post}",
        post_date=_NOW - timedelta(days=days_ago),
        raw_text=text,
        cleaned_text=text,
        **kw,
    )


def _original():
    return _v(
        _POST,
        post="1",
        days_ago=10,
        detected_salary_min=200_000.0,
        salary_currency="RUB",
        work_format="office",
        technologies=["Docker", "SQL"],
    )


def _edited(post="9", days_ago=1):
    return _v(
        _EDITED,
        post=post,
        days_ago=days_ago,
        detected_salary_min=240_000.0,
        salary_currency="RUB",
        work_format="remote",
        technologies=["Kafka", "SQL"],
    )


def test_run_records_changes_and_history_api(api_client):
    with session_scope() as s:
        run1, run2 = new_parse_run(s), new_parse_run(s)
    original = _original()
    with session_scope() as s:
        repo.upsert_vacancies(s, [original], parse_run_id=run1)
    repost = _edited()
    repost.is_duplicate, repost.duplicate_of, repost.duplicate_reason = True, original.id, "fuzzy_text:97"
    same = _v(_POST, post="5", days_ago=5)  # тот же текст в том же канале позже
    same.is_duplicate, same.duplicate_of, same.duplicate_reason = True, original.id, "exact_raw_hash"
    with session_scope() as s:
        repo.upsert_vacancies(s, [same, repost], parse_run_id=run2)

    token = api_client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": "secret1"}).json()[
        "access_token"
    ]
    r = api_client.get(f"/api/v1/vacancies/{original.id}/history", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    h = r.json()
    assert [(x["kind"], x["post_url"]) for x in h["sightings"]] == [
        ("original", "https://t.me/qa_jobs/1"),
        ("repost", "https://t.me/qa_jobs/5"),
        ("edited", "https://t.me/qa_jobs/9"),
    ]
    assert h["edits"] == 1 and h["duplicate_of"] is None
    fields = {c["field"] for c in h["sightings"][2]["changes"]}
    assert fields == {"salary", "work_format", "technologies"}
    assert h["sightings"][1]["changes"] == []
    with session_scope() as s:
        # «Изменений нет» — SQL NULL, а не JSON null: иначе `changes IS NOT NULL` считает все появления.
        nulls = s.scalar(text("select count(*) from vacancy_sightings where changes is null"))
        assert nulls == 2
    assert (
        api_client.get("/api/v1/vacancies/nope/history", headers={"Authorization": f"Bearer {token}"}).status_code
        == 404
    )


def test_merge_duplicates_records_changes(api_client):
    with session_scope() as s:
        run1, run2 = new_parse_run(s), new_parse_run(s)
    original, copy = _original(), _edited(post="7", days_ago=3)
    with session_scope() as s:
        repo.upsert_vacancies(s, [original], parse_run_id=run1)
    with session_scope() as s:
        repo.upsert_vacancies(s, [copy], parse_run_id=run2)
    with session_scope() as s:
        pairs = find_near_duplicates(s, AppConfig())
        assert [(p.duplicate_id, p.canonical_id) for p in pairs] == [(copy.id, original.id)]
        apply_merges(s, pairs)
    with session_scope() as s:
        sighting = s.get(VacancySightingRow, "qa_jobs-7")
        assert sighting.vacancy_id == original.id
        assert {c["field"] for c in sighting.changes} == {"salary", "work_format", "technologies"}

    token = api_client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": "secret1"}).json()[
        "access_token"
    ]
    hidden = api_client.get(f"/api/v1/vacancies/{copy.id}/history", headers={"Authorization": f"Bearer {token}"})
    assert hidden.json()["duplicate_of"] == original.id and hidden.json()["sightings"] == []
