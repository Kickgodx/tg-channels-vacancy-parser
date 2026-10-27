"""Жизнь вакансии: срок по публикациям в Telegram, разрезы, компании и долгожители."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.db.models import VacancyRow, VacancySightingRow
from backend.db.vacancy_lifetime import MIN_GROUP, vacancy_lifetime
from backend.schemas import VacancyQuery
from tg_jobs_parser.utils.company import company_key

_ids = iter(range(1, 1_000_000))
_DAY = datetime(2026, 9, 1, 10, tzinfo=UTC)


def _vacancy(
    *,
    company: str | None = "Ромашка",
    roles: list[str] | None = None,
    grade: str = "middle",
    channel: str = "ch",
    duplicate: bool = False,
) -> VacancyRow:
    i = next(_ids)
    return VacancyRow(
        id=f"lt-{i}",
        source_post_key=f"{channel}-{i}",
        source_channel=channel,
        source_url="https://t.me/s/ch",
        post_date=_DAY,
        raw_text="t",
        cleaned_text="t",
        company=company,
        company_key=company_key(company),
        detected_specializations=roles if roles is not None else ["QA"],
        technologies=[],
        detected_grade=grade,
        is_duplicate=duplicate,
    )


def _add(session, vacancy: VacancyRow, offsets_days: list[float]) -> VacancyRow:
    """Вакансия и её публикации со сдвигом от ``_DAY`` (первая — оригинал)."""
    session.add(vacancy)
    session.flush()
    for n, off in enumerate(offsets_days):
        session.add(
            VacancySightingRow(
                source_post_key=f"s-{vacancy.id}-{n}",
                vacancy_id=vacancy.id,
                source_channel=vacancy.source_channel,
                post_date=_DAY + timedelta(days=off),
                is_duplicate=n > 0,
            )
        )
    return vacancy


def test_span_days_buckets_and_longest(db_session):
    long_one = _add(db_session, _vacancy(company="Долгострой"), [0, 10, 25])
    _add(db_session, _vacancy(), [0, 0.1])  # перепост в тот же день — не повторная публикация
    week = _add(db_session, _vacancy(), [0, 5])
    _add(db_session, _vacancy(), [0])
    # Без публикаций (ручной импорт, HH) в расчёт не входит.
    db_session.add(_vacancy(channel="hh"))
    db_session.commit()

    r = vacancy_lifetime(db_session, VacancyQuery())
    assert (r.vacancies, r.republished, r.long_open) == (4, 2, 1)
    assert r.republished_share == 0.5
    assert r.span_p50_days == 15.0  # медиана 5 и 25 дней
    assert {b.label: b.count for b in r.buckets} == {
        "В один день": 2,
        "До недели": 1,
        "1–2 недели": 0,
        "2–4 недели": 1,
        "Больше месяца": 0,
    }
    assert [v.id for v in r.longest] == [long_one.id, week.id]
    top = r.longest[0]
    assert (top.span_days, top.publication_days, top.posts, top.company) == (25, 3, 3, "Долгострой")
    assert [c.company for c in r.companies] == ["Долгострой", "Ромашка"]
    assert r.companies[1].vacancies == 3


def test_groups_need_min_vacancies_and_respect_filters(db_session):
    for i in range(MIN_GROUP):
        _add(db_session, _vacancy(roles=["Backend"], grade="senior"), [0, 3] if i < 2 else [0])
    for _ in range(MIN_GROUP - 1):
        _add(db_session, _vacancy(roles=["QA"], grade="junior"), [0])
    _add(db_session, _vacancy(roles=["Backend"], duplicate=True), [0, 40])
    db_session.commit()

    r = vacancy_lifetime(db_session, VacancyQuery())
    assert [g.name for g in r.by_role] == ["Backend"]
    backend = r.by_role[0]
    assert (backend.vacancies, backend.republished, backend.median_span_days) == (MIN_GROUP, 2, 3.0)
    assert [g.name for g in r.by_grade] == ["senior"]

    only_qa = vacancy_lifetime(db_session, VacancyQuery(specializations=["QA"]))
    assert (only_qa.vacancies, only_qa.republished, only_qa.longest) == (MIN_GROUP - 1, 0, [])


def test_endpoint(admin_client, db_session):
    _add(db_session, _vacancy(), [0, 30])
    db_session.commit()
    r = admin_client.get("/api/v1/analytics/lifetime", params={"include_archived": "true"})
    assert r.status_code == 200
    body = r.json()
    assert (body["vacancies"], body["long_open"], body["long_open_days"]) == (1, 1, 21)
    assert body["longest"][0]["span_days"] == 30
