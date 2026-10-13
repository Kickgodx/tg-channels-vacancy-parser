"""Запись вакансий в БД: content-id и skip дубликатов по тексту."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.db.models import VacancyRow
from backend.db.repository import (
    VACANCY_TTL_DAYS,
    archive_expired_vacancies,
    list_vacancies,
    upsert_vacancies,
)
from backend.schemas import VacancyQuery
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 't.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _vac(
    *,
    cleaned: str,
    source_post_key: str,
    company: str | None = None,
    parsed_at: datetime | None = None,
    post_date: datetime | None = None,
) -> Vacancy:
    return Vacancy(
        id=vacancy_content_id(cleaned),
        source_post_key=source_post_key,
        source_channel=source_post_key.split("-", 1)[0],
        source_url="https://t.me/s/ch",
        post_url=f"https://t.me/{source_post_key.replace('-', '/', 1)}",
        post_date=post_date or datetime.now(UTC),
        raw_text=cleaned,
        cleaned_text=cleaned,
        company=company,
        parsed_at=parsed_at or datetime.now(UTC),
    )


def test_upsert_sets_updated_at(db_session):
    run_id = uuid.uuid4()
    text = "Python backend remote"
    v = _vac(cleaned=text, source_post_key="chan-1")
    stats = upsert_vacancies(db_session, [v], parse_run_id=run_id)
    db_session.commit()
    assert stats.inserted == 1
    assert stats.updated_existing == 0

    run_id2 = uuid.uuid4()
    v2 = _vac(cleaned=text, source_post_key="chan-2", company="Acme")
    stats2 = upsert_vacancies(db_session, [v2], parse_run_id=run_id2)
    db_session.commit()
    assert stats2.inserted == 0
    assert stats2.updated_existing == 1

    row = db_session.get(VacancyRow, vacancy_content_id(text))
    assert row is not None
    assert row.company is None
    assert row.last_parse_run_id == run_id2
    assert row.source_post_key == "chan-1"


def test_upsert_long_required_experience(db_session):
    """Ответ LLM может быть длиннее legacy VARCHAR(128)."""
    run_id = uuid.uuid4()
    long_exp = (
        "7+ years of backend development experience in Node.js/C#/.NET "
        "(.Net is must, Node is a big advantage) 4+ years of experience in React."
    )
    assert len(long_exp) > 128
    cleaned = "x"
    v = Vacancy(
        id=vacancy_content_id(cleaned),
        source_post_key="DarikaINITVacancies-930",
        source_channel="DarikaINITVacancies",
        source_url="https://t.me/s/DarikaINITVacancies",
        post_url="https://t.me/DarikaINITVacancies/930",
        post_date=datetime.now(UTC),
        raw_text="x",
        cleaned_text=cleaned,
        required_experience=long_exp,
    )
    stats = upsert_vacancies(db_session, [v], parse_run_id=run_id)
    db_session.commit()
    assert stats.inserted == 1


def test_upsert_two_distinct_texts_two_rows(db_session):
    run_id = uuid.uuid4()
    v1 = _vac(cleaned="Python dev", source_post_key="a-1")
    v2 = _vac(cleaned="Go dev", source_post_key="a-2")
    upsert_vacancies(db_session, [v1, v2], parse_run_id=run_id)
    db_session.commit()
    count = db_session.scalar(select(func.count()).select_from(VacancyRow))
    assert count == 2


def test_archive_expired_vacancies_archives_records_beyond_ttl(db_session):
    now = datetime(2026, 9, 7, tzinfo=UTC)
    expired = _vac(
        cleaned="expired vacancy",
        source_post_key="old-1",
        parsed_at=now,
        post_date=now - timedelta(days=VACANCY_TTL_DAYS, seconds=1),
    )
    current = _vac(
        cleaned="current vacancy",
        source_post_key="new-1",
        parsed_at=now,
        post_date=now - timedelta(days=VACANCY_TTL_DAYS - 1),
    )
    missing_post_date = _vac(
        cleaned="expired without post date",
        source_post_key="unknown-1",
        parsed_at=now - timedelta(days=VACANCY_TTL_DAYS, seconds=1),
    ).model_copy(update={"post_date": None})
    upsert_vacancies(
        db_session,
        [expired, current, missing_post_date],
        parse_run_id=uuid.uuid4(),
    )
    db_session.commit()

    archived = archive_expired_vacancies(db_session, now=now)
    db_session.commit()

    assert archived == 2
    assert db_session.get(VacancyRow, expired.id).archived_at is not None
    assert db_session.get(VacancyRow, missing_post_date.id).archived_at is not None
    current_row = db_session.get(VacancyRow, current.id)
    assert current_row is not None
    assert current_row.archived_at is None

    assert archive_expired_vacancies(db_session, now=now) == 0


def test_list_vacancies_hides_archived_unless_included(db_session):
    now = datetime.now(UTC)
    old = _vac(
        cleaned="old vacancy",
        source_post_key="old-1",
        post_date=now - timedelta(days=VACANCY_TTL_DAYS + 1),
    )
    fresh = _vac(cleaned="fresh vacancy", source_post_key="new-1", post_date=now)
    upsert_vacancies(db_session, [old, fresh], parse_run_id=uuid.uuid4())
    archive_expired_vacancies(db_session, now=now)
    db_session.commit()

    items, total = list_vacancies(db_session, VacancyQuery())
    assert total == 1
    assert [v.id for v in items] == [fresh.id]

    items, total = list_vacancies(db_session, VacancyQuery(include_archived=True))
    assert total == 2


def _archived_vacancy(db_session, *, text: str, first_seen: datetime) -> Vacancy:
    """Вакансия, ушедшая в архив по TTL от первой публикации."""
    original = _vac(cleaned=text, source_post_key="chan-1", post_date=first_seen)
    upsert_vacancies(db_session, [original], parse_run_id=uuid.uuid4())
    assert archive_expired_vacancies(db_session) == 1
    db_session.commit()
    return original


def test_repost_advances_post_date_and_unarchives(db_session):
    """Канал повторил тот же текст: вакансия актуальна, архив по первой публикации снимается."""
    now = datetime.now(UTC)
    text = "Python backend, remote, weekly repost"
    original = _archived_vacancy(db_session, text=text, first_seen=now - timedelta(days=VACANCY_TTL_DAYS + 5))

    repost_date = now - timedelta(days=1)
    repost = _vac(cleaned=text, source_post_key="chan-99", post_date=repost_date)
    stats = upsert_vacancies(db_session, [repost], parse_run_id=uuid.uuid4())
    db_session.commit()
    db_session.expire_all()

    assert stats.updated_existing == 1
    row = db_session.get(VacancyRow, original.id)
    assert row.archived_at is None
    assert row.post_date.replace(tzinfo=UTC) == repost_date
    # TTL теперь считается от свежей публикации — повторная архивация не трогает.
    assert archive_expired_vacancies(db_session) == 0
    items, total = list_vacancies(db_session, VacancyQuery())
    assert total == 1 and items[0].id == original.id


@pytest.mark.parametrize("days_before_first", [0, 3])
def test_same_or_older_post_keeps_date_and_archive(db_session, days_before_first):
    """Повторный разбор того же или более старого поста — не репост: дата и архив не меняются."""
    now = datetime.now(UTC)
    first_seen = now - timedelta(days=VACANCY_TTL_DAYS + 5)
    text = "Go developer, office"
    original = _archived_vacancy(db_session, text=text, first_seen=first_seen)

    again = _vac(
        cleaned=text,
        source_post_key="chan-2",
        post_date=first_seen - timedelta(days=days_before_first),
    )
    upsert_vacancies(db_session, [again], parse_run_id=uuid.uuid4())
    db_session.commit()
    db_session.expire_all()

    row = db_session.get(VacancyRow, original.id)
    assert row.archived_at is not None
    assert row.post_date.replace(tzinfo=UTC) == first_seen


def test_repost_fills_missing_post_date(db_session):
    text = "QA engineer, hybrid"
    undated = _vac(cleaned=text, source_post_key="chan-1").model_copy(update={"post_date": None})
    upsert_vacancies(db_session, [undated], parse_run_id=uuid.uuid4())
    db_session.commit()

    dated_at = datetime.now(UTC) - timedelta(days=2)
    dated = _vac(cleaned=text, source_post_key="chan-2", post_date=dated_at)
    upsert_vacancies(db_session, [dated], parse_run_id=uuid.uuid4())
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(VacancyRow, undated.id).post_date.replace(tzinfo=UTC) == dated_at


def test_hh_republished_vacancy_returns_from_archive(db_session):
    """HH поднял вакансию заново (published_at новее) — она возвращается из архива."""
    from backend.db import hh_repository as hh_repo
    from tg_jobs_parser.config.schema import FxConfig

    now = datetime.now(UTC)
    fx = FxConfig(enabled=False)
    first = _vac(
        cleaned="HH: Python developer",
        source_post_key="hh-555",
        post_date=now - timedelta(days=VACANCY_TTL_DAYS + 5),
    )
    hh_repo.upsert_hh_vacancies(db_session, [first], fx_config=fx)
    assert archive_expired_vacancies(db_session) == 1
    db_session.commit()

    # Тот же published_at (вакансия просто снова попала в выдачу) — архив остаётся.
    hh_repo.upsert_hh_vacancies(db_session, [first.model_copy()], fx_config=fx)
    db_session.commit()
    assert db_session.get(VacancyRow, first.id).archived_at is not None

    republished = first.model_copy(update={"post_date": now - timedelta(hours=3)})
    stats = hh_repo.upsert_hh_vacancies(db_session, [republished], fx_config=fx)
    db_session.commit()

    assert stats.updated_existing == 1
    assert db_session.get(VacancyRow, first.id).archived_at is None
