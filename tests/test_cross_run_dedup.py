"""Дедуп между прогонами: перепост вакансии из БД, выборка «известных», разовое склеивание."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from tests.conftest import new_parse_run

from backend.db import repository as repo
from backend.db.models import ParseRunVacancyRow, VacancyRow, VacancySightingRow
from backend.db.session import session_scope
from backend.services.near_duplicate_merge import apply_merges, find_near_duplicates, merge_all
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id

_POST = (
    "Компания Acme ищет QA инженера в команду платежей. Удалёнка, полный день. Требования: "
    "pytest, API, SQL, Docker, опыт от 3 лет. Задачи: автотесты, ревью, CI. Зарплата 200 000 ₽. "
    "Пишите в телеграм @acme_hr, ответим за день."
)
_EDITED = _POST.replace("200 000", "240 000")
_NOW = datetime.now(UTC).replace(microsecond=0)


def _v(text: str, *, post: str, days_ago: int, channel="qa_jobs", **kw) -> Vacancy:
    kw.setdefault("detected_specializations", ["QA"])
    kw.setdefault("company", "Acme")
    return Vacancy(
        id=vacancy_content_id(text),
        source_post_key=f"{channel}-{post}",
        source_channel=channel,
        source_url=f"https://t.me/s/{channel}",
        post_url=f"https://t.me/{channel}/{post}",
        post_date=_NOW - timedelta(days=days_ago),
        raw_text=text,
        cleaned_text=text,
        **kw,
    )


def _store(vacancies: list[Vacancy], run_id: uuid.UUID) -> None:
    with session_scope() as s:
        repo.upsert_vacancies(s, vacancies, parse_run_id=run_id)


def test_repost_of_db_vacancy_touches_it_and_records_sighting(db_session):
    old_run, new_run = new_parse_run(db_session), new_parse_run(db_session)
    db_session.commit()
    original = _v(_POST, post="1", days_ago=20)
    _store([original], old_run)
    with session_scope() as s:
        s.get(VacancyRow, original.id).archived_at = _NOW  # ушла в архив по TTL

    repost = _v(_EDITED, post="9", days_ago=1)
    repost.is_duplicate, repost.duplicate_of, repost.duplicate_reason = True, original.id, "fuzzy_text:99"
    _store([repost], new_run)

    with session_scope() as s:
        row = s.get(VacancyRow, original.id)
        assert row.post_date == repost.post_date and row.archived_at is None
        assert row.last_parse_run_id == new_run
        assert s.get(VacancyRow, repost.id) is None  # новой вакансии нет
        sighting = s.get(VacancySightingRow, "qa_jobs-9")
        assert (sighting.vacancy_id, sighting.is_duplicate, sighting.duplicate_reason) == (
            original.id,
            True,
            "fuzzy_text:99",
        )
        runs = set(
            s.scalars(select(ParseRunVacancyRow.parse_run_id).where(ParseRunVacancyRow.vacancy_id == original.id))
        )
        assert runs == {old_run, new_run}


def test_recent_vacancies_for_dedup(db_session):
    run = new_parse_run(db_session)
    db_session.commit()
    fresh = _v(_POST, post="1", days_ago=3)
    old = _v(_POST + " старая", post="2", days_ago=50)
    hh = _v(_POST + " hh", post="3", days_ago=1, channel="hh")
    _store([fresh, old, hh], run)
    with session_scope() as s:
        got = repo.recent_vacancies_for_dedup(s, days=40)
    assert [v.id for v in got] == [fresh.id]


def test_merge_duplicates_once(db_session):
    run_a, run_b = new_parse_run(db_session), new_parse_run(db_session)
    db_session.commit()
    original = _v(_POST, post="1", days_ago=20)
    other_grade = _v(_POST.replace("QA инженера", "QA инженера уровня senior"), post="2", days_ago=10)
    other_grade.detected_grade, original.detected_grade = "senior", "junior"
    _store([original], run_a)
    _store([other_grade], run_a)
    copy = _v(_EDITED, post="3", days_ago=5, detected_grade="junior")
    _store([copy], run_b)  # до дедупа между прогонами — отдельная вакансия

    cfg = AppConfig()
    with session_scope() as s:
        pairs = find_near_duplicates(s, cfg)
        assert [(p.duplicate_id, p.canonical_id) for p in pairs] == [(copy.id, original.id)]
        assert pairs[0].reason.startswith("fuzzy_text:")
        assert apply_merges(s, pairs) == 1
    with session_scope() as s:
        hidden = s.get(VacancyRow, copy.id)
        assert (hidden.is_duplicate, hidden.duplicate_of) == (True, original.id)
        canon = s.get(VacancyRow, original.id)
        assert canon.post_date == copy.post_date
        sighting = s.get(VacancySightingRow, "qa_jobs-3")
        assert sighting.vacancy_id == original.id and sighting.is_duplicate
        runs = set(
            s.scalars(select(ParseRunVacancyRow.parse_run_id).where(ParseRunVacancyRow.vacancy_id == original.id))
        )
        assert runs == {run_a, run_b}
        assert not s.get(VacancyRow, other_grade.id).is_duplicate  # другой грейд — другая позиция
        # Повторный запуск ничего не находит.
        assert find_near_duplicates(s, cfg) == []


def test_parse_job_passes_known_vacancies(admin_client, monkeypatch):
    import time

    from backend.settings import get_settings
    from tg_jobs_parser.models.parse_result import ParseResult

    run = None
    with session_scope() as s:
        from backend.db.models import ParseRunRow

        row = ParseRunRow(status="completed", config_snapshot={})
        s.add(row)
        s.flush()
        run = row.id
    _store([_v(_POST, post="1", days_ago=2)], run)
    seen: dict = {}

    def fake_run_parse(config, **kwargs):
        seen["known"] = [v.id for v in kwargs.get("known_vacancies") or []]
        return ParseResult(posts_fetched=0)

    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    monkeypatch.setattr("backend.workers.parse_tasks.run_parse", fake_run_parse)
    run_id = admin_client.post("/api/v1/runs", json={}).json()["run_id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and "known" not in seen:
        time.sleep(0.05)
    while time.monotonic() < deadline and admin_client.get(f"/api/v1/runs/{run_id}").json()["status"] == "running":
        time.sleep(0.05)
    assert seen["known"] == [vacancy_content_id(_POST)]


def test_merge_all_follows_chain_through_moved_date(db_session):
    """A (−60 дн.), копия B (−30): склейка сдвигает дату A на −30, и в окно попадает копия C (−5)."""
    run = new_parse_run(db_session)
    db_session.commit()
    a = _v(_POST, post="1", days_ago=60)
    b = _v(_EDITED, post="2", days_ago=30)
    c = _v(_POST.replace("200 000", "260 000"), post="3", days_ago=5)
    for v in (a, b, c):
        _store([v], run)
    with session_scope() as s:
        assert [(p.duplicate_id, p.canonical_id) for p in find_near_duplicates(s, AppConfig())] == [(b.id, a.id)]
        assert merge_all(s, AppConfig()) == (2, 2)
    with session_scope() as s:
        assert {s.get(VacancyRow, x.id).duplicate_of for x in (b, c)} == {a.id}
        assert find_near_duplicates(s, AppConfig()) == []


def test_flatten_hidden_copies_repoints_chain(db_session):
    from backend.services.near_duplicate_merge import flatten_hidden_copies

    run = new_parse_run(db_session)
    db_session.commit()
    a = _v(_POST, post="1", days_ago=30, detected_salary_min=250_000.0, salary_currency="RUB")
    b = _v(_EDITED, post="2", days_ago=20, detected_salary_min=200_000.0, salary_currency="RUB")
    c = _v(
        _POST.replace("200 000", "260 000"), post="3", days_ago=10, detected_salary_min=260_000.0, salary_currency="RUB"
    )
    for v in (a, b, c):
        _store([v], run)
    with session_scope() as s:  # цепочка, как после двух проходов: C → B → A
        s.get(VacancyRow, b.id).is_duplicate, s.get(VacancyRow, b.id).duplicate_of = True, a.id
        s.get(VacancyRow, c.id).is_duplicate, s.get(VacancyRow, c.id).duplicate_of = True, b.id
    with session_scope() as s:
        assert flatten_hidden_copies(s) == 1
        assert flatten_hidden_copies(s) == 0
    with session_scope() as s:
        assert s.get(VacancyRow, c.id).duplicate_of == a.id
        sighting = s.get(VacancySightingRow, "qa_jobs-3")
        assert sighting.vacancy_id == a.id
        assert sighting.changes[0]["old"]["min"] == 250_000.0 and sighting.changes[0]["new"]["min"] == 260_000.0
