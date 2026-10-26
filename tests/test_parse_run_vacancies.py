"""Связка parse_run_vacancies: история прогона не пустеет после следующих прогонов."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from tests.conftest import add_legacy_vacancy

from backend.db import parse_run_repository as prr
from backend.db import vacancy_repository as vr
from backend.db.migrate import alembic_ini_path
from backend.db.models import ParseRunRow, ParseRunVacancyRow, VacancyRow
from backend.db.transfer import import_dump_file, write_dump_file
from backend.schemas import VacancyQuery
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.vacancy import Vacancy

FX_OFF = FxConfig(enabled=False)


@pytest.fixture
def db(db_session):
    return db_session


def _v(i: int) -> Vacancy:
    return Vacancy(
        id=f"v{i:04d}",
        source_post_key=f"chan-{i}",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url=f"https://t.me/chan/{i}",
        post_date=datetime.now(UTC),
        raw_text=f"text {i}",
        cleaned_text=f"text {i}",
    )


def _run(session, *, finished_at: datetime | None = None) -> uuid.UUID:
    row = ParseRunRow(status="completed", config_snapshot={}, finished_at=finished_at or datetime.now(UTC))
    session.add(row)
    session.flush()
    return row.id


def _run_ids(session, run_id: uuid.UUID) -> set[str]:
    items, total = vr.list_run_vacancies(session, run_id, VacancyQuery())
    assert total == vr.count_run_vacancies(session, run_id) == len(items)
    return {i.id for i in items}


def test_earlier_run_keeps_vacancies_seen_again(db):
    first, second = _run(db), _run(db)
    vr.upsert_vacancies(db, [_v(1), _v(2)], parse_run_id=first, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v(2), _v(3)], parse_run_id=second, fx_config=FX_OFF)
    db.commit()

    assert _run_ids(db, first) == {"v0001", "v0002"}
    assert _run_ids(db, second) == {"v0002", "v0003"}
    exported = {v.id for v in vr.iter_export_run_vacancies(db, first, VacancyQuery(), limit=100)}
    assert exported == {"v0001", "v0002"}
    assert db.get(VacancyRow, "v0002").last_parse_run_id == second


def test_run_keeps_archived_vacancies(db):
    run = _run(db)
    vr.upsert_vacancies(db, [_v(1), _v(2)], parse_run_id=run, fx_config=FX_OFF)
    db.execute(VacancyRow.__table__.update().where(VacancyRow.id == "v0001").values(archived_at=datetime.now(UTC)))
    db.commit()

    assert _run_ids(db, run) == {"v0001", "v0002"}
    items, _ = vr.list_run_vacancies(db, run, VacancyQuery())
    assert {i.id for i in items if i.archived_at is not None} == {"v0001"}
    assert len(list(vr.iter_export_run_vacancies(db, run, VacancyQuery(), limit=100))) == 2


def test_same_run_batches_do_not_duplicate_links(db):
    run = _run(db)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=run, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v(1), _v(2)], parse_run_id=run, fx_config=FX_OFF)
    db.commit()
    assert len(db.scalars(select(ParseRunVacancyRow)).all()) == 2


def test_manual_import_adds_no_links(db):
    run = _run(db)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=run, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v(1), _v(2)], parse_run_id=None, fx_config=FX_OFF)
    db.commit()
    assert _run_ids(db, run) == {"v0001"}


def test_purged_run_takes_its_links_but_not_vacancies(db):
    old = _run(db, finished_at=datetime.now(UTC) - timedelta(days=400))
    fresh = _run(db)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=old, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=fresh, fx_config=FX_OFF)
    db.commit()

    assert prr.purge_old_parse_runs(db, older_than=datetime.now(UTC) - timedelta(days=30)) == 1
    db.commit()
    links = db.execute(select(ParseRunVacancyRow.parse_run_id, ParseRunVacancyRow.vacancy_id)).all()
    assert links == [(fresh, "v0001")]
    assert db.get(VacancyRow, "v0001") is not None


def test_migration_backfills_links_from_last_parse_run_id(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "025_refresh_tokens")

    engine = create_engine(pg_empty_db)
    run_id = uuid.uuid4()
    with sessionmaker(bind=engine)() as s:
        s.add(ParseRunRow(id=run_id, status="completed", config_snapshot={}))
        for vid, last_run in (("bound", run_id), ("purged-run", uuid.uuid4()), ("manual", None)):
            s.flush()
            add_legacy_vacancy(
                s,
                id=vid,
                source_post_key=vid,
                source_channel="chan",
                source_url="https://t.me/s/chan",
                raw_text=vid,
                cleaned_text=vid,
                last_parse_run_id=last_run,
            )
        s.commit()

    command.upgrade(cfg, "head")
    with sessionmaker(bind=engine)() as s:
        links = s.execute(select(ParseRunVacancyRow.parse_run_id, ParseRunVacancyRow.vacancy_id)).all()
    engine.dispose()
    assert links == [(run_id, "bound")]


def test_dump_roundtrip_keeps_links_and_old_dump_is_backfilled(db, tmp_path):
    first, second = _run(db), _run(db)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=first, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=second, fx_config=FX_OFF)
    db.commit()
    dump = tmp_path / "dump.json"
    write_dump_file(db, dump)

    stats = import_dump_file(db, dump, mode="replace")
    db.commit()
    assert stats.parse_run_vacancies == 2
    assert _run_ids(db, first) == _run_ids(db, second) == {"v0001"}

    # Дамп до связки: восстанавливается то, что знал last_parse_run_id.
    payload = json.loads(dump.read_text(encoding="utf-8"))
    del payload["tables"]["parse_run_vacancies"]
    dump.write_text(json.dumps(payload), encoding="utf-8")
    stats = import_dump_file(db, dump, mode="replace")
    db.commit()
    assert stats.parse_run_vacancies == 1
    assert _run_ids(db, first) == set()
    assert _run_ids(db, second) == {"v0001"}
