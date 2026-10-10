"""Сохранённые фильтры (CRUD) и FTS-условие поиска."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from backend.db import repository as repo
from backend.db.base import Base
from backend.db.models import VacancyRow
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.schemas import VacancyQuery


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


def test_saved_filters_crud(db_session):
    assert repo.list_saved_filters(db_session) == []

    row = repo.create_saved_filter(
        db_session,
        name="Remote Python",
        params=VacancyQuery(work_format="remote", q="python").model_dump(mode="json"),
    )
    repo.create_saved_filter(db_session, name="AAA", params={})
    db_session.commit()

    rows = repo.list_saved_filters(db_session)
    assert [r.name for r in rows] == ["AAA", "Remote Python"]
    assert rows[1].params["work_format"] == "remote"

    assert repo.delete_saved_filter(db_session, row.id) is True
    assert repo.delete_saved_filter(db_session, row.id) is False
    assert [r.name for r in repo.list_saved_filters(db_session)] == ["AAA"]


def test_fts_condition_compiles_for_postgresql():
    stmt = apply_vacancy_filters(
        select(VacancyRow.id),
        VacancyQuery(q="python разработчик"),
        dialect="postgresql",
    )
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "to_tsvector" in sql
    assert "websearch_to_tsquery" in sql
    assert "@@" in sql
    assert "russian" in sql


def test_q_filter_falls_back_to_ilike_on_sqlite(db_session):
    db_session.add_all(
        [
            VacancyRow(
                id="a",
                source_post_key="ch-1",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Senior Python developer needed",
                cleaned_text="Senior Python developer needed",
            ),
            VacancyRow(
                id="b",
                source_post_key="ch-2",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Java backend engineer",
                cleaned_text="Java backend engineer",
            ),
        ]
    )
    db_session.commit()

    items, total = repo.list_vacancies(db_session, VacancyQuery(q="python"))
    assert total == 1
    assert items[0].id == "a"
