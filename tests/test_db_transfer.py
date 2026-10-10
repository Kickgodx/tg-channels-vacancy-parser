"""Дамп и импорт БД."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.base import Base
from backend.db.models import ExchangeRateRow, ParseRunRow, VacancyRow
from backend.db.transfer import (
    import_dump_file,
    load_dump_file,
    write_dump_file,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'transfer.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed(session) -> uuid.UUID:
    run_id = uuid.uuid4()
    session.add(
        ParseRunRow(
            id=run_id,
            status="completed",
            config_snapshot={"channels": ["test"]},
            posts_fetched=10,
        )
    )
    session.add(
        ExchangeRateRow(
            rate_date=date(2026, 5, 1),
            currency="USD",
            rate_to_rub=90.5,
        )
    )
    session.add(
        VacancyRow(
            id="vac-1",
            source_post_key="ch-1",
            source_channel="ch",
            source_url="https://t.me/s/ch/1",
            raw_text="text",
            cleaned_text="text",
            last_parse_run_id=run_id,
        )
    )
    session.commit()
    return run_id


def test_dump_import_roundtrip_merge(db_session, tmp_path):
    run_id = _seed(db_session)
    dump_path = tmp_path / "dump.json"
    write_dump_file(db_session, dump_path)
    db_session.query(VacancyRow).delete()
    db_session.query(ParseRunRow).delete()
    db_session.commit()
    assert db_session.query(VacancyRow).count() == 0

    stats = import_dump_file(db_session, dump_path, mode="merge")
    db_session.commit()
    assert stats.vacancies == 1
    assert stats.parse_runs == 1

    row = db_session.get(VacancyRow, "vac-1")
    assert row is not None
    assert row.last_parse_run_id == run_id


def test_import_replace_overwrites(db_session, tmp_path):
    _seed(db_session)
    dump_path = tmp_path / "dump.json"
    write_dump_file(db_session, dump_path)

    db_session.add(
        VacancyRow(
            id="other",
            source_post_key="x",
            source_channel="x",
            source_url="https://t.me/x",
            raw_text="x",
            cleaned_text="x",
        )
    )
    db_session.commit()

    import_dump_file(db_session, dump_path, mode="replace")
    db_session.commit()
    ids = {r.id for r in db_session.query(VacancyRow).all()}
    assert ids == {"vac-1"}


def test_load_dump_rejects_unknown_version(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"format_version": 99, "tables": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="версия"):
        load_dump_file(path)
