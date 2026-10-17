"""Частичный индекс активных вакансий и корректный total при пагинации."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import inspect, text
from tests.conftest import new_parse_run

from backend.db.repository import list_vacancies, upsert_vacancies
from backend.schemas import VacancyQuery
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id


def _vac(cleaned: str, *, days_ago: int = 0) -> Vacancy:
    return Vacancy(
        id=vacancy_content_id(cleaned),
        source_post_key=f"ch-{cleaned[:8]}",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url=f"https://t.me/chan/{days_ago}",
        post_date=datetime.now(UTC) - timedelta(days=days_ago),
        raw_text=cleaned,
        cleaned_text=cleaned,
        detected_grade="middle",
        work_format="remote",
    )


def test_active_post_date_partial_index_exists(db_session):
    names = {idx["name"] for idx in inspect(db_session.get_bind()).get_indexes("vacancies")}
    assert "ix_vacancies_active_post_date" in names


def test_list_vacancies_total_past_last_page(db_session):
    items = [_vac(f"vacancy text number {i}", days_ago=i) for i in range(5)]
    upsert_vacancies(db_session, items, parse_run_id=new_parse_run(db_session))
    db_session.commit()

    page, total = list_vacancies(db_session, VacancyQuery(page=1, page_size=2))
    assert total == 5
    assert len(page) == 2

    empty, total2 = list_vacancies(db_session, VacancyQuery(page=10, page_size=2))
    assert total2 == 5
    assert empty == []


def test_list_default_filter_can_use_partial_index(db_session):
    """Запрос списка по умолчанию совпадает с частичным индексом (миграция 013)."""
    upsert_vacancies(
        db_session,
        [_vac(f"indexed vacancy {i}", days_ago=i) for i in range(3)],
        parse_run_id=new_parse_run(db_session),
    )
    db_session.commit()
    # На трёх строках планировщик выбрал бы seq scan — проверяем, что индекс вообще подходит.
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = db_session.execute(
        text(
            "EXPLAIN SELECT id FROM vacancies "
            "WHERE is_duplicate IS false AND archived_at IS NULL "
            "ORDER BY post_date DESC NULLS LAST, id ASC LIMIT 2"
        )
    ).scalars()
    plan_text = "\n".join(plan)
    assert "ix_vacancies_active_post_date" in plan_text
    assert "Sort" not in plan_text


def test_vacancy_count_cache_reuses_total(db_session, monkeypatch):
    from backend.db import vacancy_repository as repo

    monkeypatch.setattr(repo, "_COUNT_CACHE_TTL_S", 60.0)
    upsert_vacancies(
        db_session,
        [_vac(f"cache me {i}", days_ago=i) for i in range(3)],
        parse_run_id=new_parse_run(db_session),
    )
    db_session.commit()
    repo.invalidate_vacancy_count_cache()

    calls = {"n": 0}
    original = repo.vacancy_count_stmt

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repo, "vacancy_count_stmt", wrapped)
    # Полная первая страница → нужен COUNT; второй вызов должен взять кэш.
    q = VacancyQuery(page=1, page_size=2)
    _, t1 = list_vacancies(db_session, q)
    _, t2 = list_vacancies(db_session, q)
    assert t1 == t2 == 3
    assert calls["n"] == 1
