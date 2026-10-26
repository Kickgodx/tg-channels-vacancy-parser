"""vacancy_sightings: появления постов, включая дубли, и доля уникальных у каналов (C5)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from tests.conftest import add_legacy_vacancy

from backend.db import analytics
from backend.db import parse_run_repository as prr
from backend.db import vacancy_repository as vr
from backend.db.migrate import alembic_ini_path
from backend.db.models import ParseRunRow, VacancyRow, VacancySightingRow
from backend.db.transfer import import_dump_file, write_dump_file
from backend.schemas import VacancyQuery
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.vacancy import Vacancy

FX_OFF = FxConfig(enabled=False)
DAY = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def db(db_session):
    return db_session


def _v(
    text: str,
    channel: str,
    post: int,
    *,
    dup_of: str | None = None,
    reason: str = "normalized_hash",
    grade: str = "middle",
    post_date: datetime = DAY,
) -> Vacancy:
    return Vacancy(
        id=f"id-{text}",
        source_post_key=f"{channel}-{post}",
        source_channel=channel,
        source_url=f"https://t.me/s/{channel}",
        post_date=post_date,
        raw_text=text,
        cleaned_text=text,
        detected_grade=grade,
        is_duplicate=dup_of is not None,
        duplicate_reason=reason if dup_of else None,
        duplicate_of=dup_of,
    )


def _run(session, *, finished_at: datetime | None = None) -> uuid.UUID:
    row = ParseRunRow(status="completed", config_snapshot={}, finished_at=finished_at or datetime.now(UTC))
    session.add(row)
    session.flush()
    return row.id


def _sightings(session) -> dict[str, tuple[str, bool, str | None]]:
    rows = session.scalars(select(VacancySightingRow)).all()
    return {r.source_post_key: (r.vacancy_id, r.is_duplicate, r.duplicate_reason) for r in rows}


def test_upsert_records_originals_and_duplicates(db):
    first, second = _run(db), _run(db)
    a1 = _v("a1", "alpha", 1)
    # В прогоне: дубль дедупа (другой канал, похожий текст) — в vacancies не пишется.
    vr.upsert_vacancies(
        db, [a1, _v("b1", "beta", 7, dup_of=a1.id, reason="fuzzy:93")], parse_run_id=first, fx_config=FX_OFF
    )
    # Следующий прогон: тот же текст постом beta и перепостом alpha — уже известная вакансия.
    vr.upsert_vacancies(db, [_v("a1", "beta", 8), _v("a1", "alpha", 2)], parse_run_id=second, fx_config=FX_OFF)
    db.commit()

    assert {r.id for r in db.scalars(select(VacancyRow)).all()} == {"id-a1"}
    assert _sightings(db) == {
        "alpha-1": ("id-a1", False, None),
        "beta-7": ("id-a1", True, "fuzzy:93"),
        "beta-8": ("id-a1", True, "seen_before"),
        "alpha-2": ("id-a1", True, "seen_before"),
    }


def test_rereading_same_post_keeps_its_first_sighting(db):
    first, retry = _run(db), _run(db)
    vr.upsert_vacancies(db, [_v("a1", "alpha", 1)], parse_run_id=first, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v("a1", "alpha", 1)], parse_run_id=retry, fx_config=FX_OFF)
    db.commit()
    assert _sightings(db) == {"alpha-1": ("id-a1", False, None)}


def test_manual_import_records_no_sightings(db):
    vr.upsert_vacancies(db, [_v("m1", "manual", 1)], parse_run_id=None, fx_config=FX_OFF)
    db.commit()
    assert _sightings(db) == {}


def test_duplicate_without_canonical_row_is_skipped(db):
    run = _run(db)
    vr.upsert_vacancies(
        db, [_v("b1", "beta", 1), _v("x", "beta", 2, dup_of="id-missing")], parse_run_id=run, fx_config=FX_OFF
    )
    db.commit()
    assert _sightings(db) == {"beta-1": ("id-b1", False, None)}


def test_purged_run_keeps_sightings(db):
    old = _run(db, finished_at=datetime.now(UTC) - timedelta(days=400))
    vr.upsert_vacancies(db, [_v("a1", "alpha", 1)], parse_run_id=old, fx_config=FX_OFF)
    db.commit()
    assert prr.purge_old_parse_runs(db, older_than=datetime.now(UTC) - timedelta(days=30)) == 1
    db.commit()
    (row,) = db.scalars(select(VacancySightingRow)).all()
    assert row.parse_run_id is None
    assert row.vacancy_id == "id-a1"


def _seed_channels(db) -> None:
    """alpha — свой контент; beta — 1 своя, 1 чужая, 1 свой перепост; gamma — только чужое."""
    run = _run(db)
    a1, a2 = _v("a1", "alpha", 1), _v("a2", "alpha", 2, grade="senior")
    b1 = _v("b1", "beta", 1, post_date=DAY + timedelta(days=10))
    vr.upsert_vacancies(
        db,
        [
            a1,
            a2,
            b1,
            _v("a1", "beta", 2, dup_of=a1.id),
            _v("b1", "beta", 3, dup_of=b1.id, post_date=DAY + timedelta(days=20)),
            _v("a2", "gamma", 1, dup_of=a2.id),
        ],
        parse_run_id=run,
        fx_config=FX_OFF,
    )
    db.commit()


def test_source_quality_counts_sightings(db):
    _seed_channels(db)
    res = {s.channel: s for s in analytics.source_quality(db, VacancyQuery())}

    assert (res["alpha"].vacancies, res["alpha"].duplicates) == (2, 0)
    assert res["alpha"].unique_share == pytest.approx(1.0)
    assert (res["beta"].vacancies, res["beta"].duplicates) == (1, 2)
    assert res["beta"].unique_share == pytest.approx(1 / 3)
    # Агрегатор без своих вакансий тоже виден в отчёте.
    assert (res["gamma"].vacancies, res["gamma"].duplicates) == (0, 1)
    assert res["gamma"].unique_share == pytest.approx(0.0)


def test_source_quality_filters_apply_to_sightings(db):
    _seed_channels(db)

    # Канал — у поста: перепост чужой вакансии считается каналу beta.
    (beta,) = analytics.source_quality(db, VacancyQuery(source_channel="@beta"))
    assert (beta.channel, beta.duplicates) == ("beta", 2)

    # Грейд — у канонической вакансии: senior — только a2 и её копия в gamma.
    res = {s.channel: s for s in analytics.source_quality(db, VacancyQuery(grade="senior"))}
    assert set(res) == {"alpha", "gamma"}
    assert res["gamma"].duplicates == 1

    # Дата — у поста: окно отсекает поздний перепост beta-3.
    window = VacancyQuery(date_from=date(2026, 9, 5), date_to=date(2026, 9, 15))
    (beta,) = analytics.source_quality(db, window)
    assert (beta.channel, beta.vacancies, beta.duplicates) == ("beta", 1, 0)


def test_migration_backfills_telegram_originals(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "026_parse_run_vacancies")

    engine = create_engine(pg_empty_db)
    run_id = uuid.uuid4()
    with sessionmaker(bind=engine)() as s:
        s.add(ParseRunRow(id=run_id, status="completed", config_snapshot={}))
        rows = (
            ("tg", "chan", "chan-1", run_id, False),
            ("tg-edited", "chan", "chan-1", run_id, False),  # тот же пост, другой текст
            ("tg-purged-run", "chan", "chan-2", uuid.uuid4(), False),
            ("hh-1", "hh", "hh-1", None, False),
            ("hh-2", "hh", "hh-2", None, True),
            ("man", "manual", "manual-x", None, False),
        )
        s.flush()
        for vid, channel, key, last_run, is_dup in rows:
            add_legacy_vacancy(
                s,
                id=vid,
                source_post_key=key,
                source_channel=channel,
                source_url="u",
                raw_text=vid,
                cleaned_text=vid,
                last_parse_run_id=last_run,
                is_duplicate=is_dup,
            )
        s.commit()

    command.upgrade(cfg, "head")
    with sessionmaker(bind=engine)() as s:
        got = {
            r.source_post_key: (r.source_channel, r.parse_run_id, r.is_duplicate)
            for r in s.scalars(select(VacancySightingRow))
        }
    engine.dispose()
    assert got == {"chan-1": ("chan", run_id, False), "chan-2": ("chan", None, False)}


def test_dump_roundtrip_keeps_sightings_and_old_dump_is_backfilled(db, tmp_path):
    _seed_channels(db)
    before = _sightings(db)
    dump = tmp_path / "dump.json"
    write_dump_file(db, dump)

    stats = import_dump_file(db, dump, mode="replace")
    db.commit()
    assert stats.vacancy_sightings == len(before) == 6
    assert _sightings(db) == before

    # Дамп до vacancy_sightings: вакансии Telegram восстанавливаются как оригиналы.
    payload = json.loads(dump.read_text(encoding="utf-8"))
    del payload["tables"]["vacancy_sightings"]
    dump.write_text(json.dumps(payload), encoding="utf-8")
    stats = import_dump_file(db, dump, mode="replace")
    db.commit()
    assert stats.vacancy_sightings == 3
    assert _sightings(db) == {k: v for k, v in before.items() if not v[1]}
