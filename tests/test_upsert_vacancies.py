"""UPSERT вакансий: updated_at не должен быть NULL."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.db.repository import upsert_vacancies
from tg_jobs_parser.models.vacancy import Vacancy


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


def test_upsert_sets_updated_at(db_session):
    run_id = uuid.uuid4()
    v = Vacancy(
        id="chan-1",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/1",
        post_date=datetime.now(timezone.utc),
        raw_text="text",
        cleaned_text="text",
    )
    n = upsert_vacancies(db_session, [v], parse_run_id=run_id)
    db_session.commit()
    assert n == 1

    v2 = v.model_copy(update={"company": "Acme"})
    upsert_vacancies(db_session, [v2], parse_run_id=run_id)
    db_session.commit()


def test_upsert_long_required_experience(db_session):
    """Ответ LLM может быть длиннее legacy VARCHAR(128)."""
    run_id = uuid.uuid4()
    long_exp = (
        "7+ years of backend development experience in Node.js/C#/.NET "
        "(.Net is must, Node is a big advantage) 4+ years of experience in React."
    )
    assert len(long_exp) > 128
    v = Vacancy(
        id="DarikaINITVacancies-930",
        source_channel="DarikaINITVacancies",
        source_url="https://t.me/s/DarikaINITVacancies",
        post_url="https://t.me/DarikaINITVacancies/930",
        post_date=datetime.now(timezone.utc),
        raw_text="x",
        cleaned_text="x",
        required_experience=long_exp,
    )
    n = upsert_vacancies(db_session, [v], parse_run_id=run_id)
    db_session.commit()
    assert n == 1
