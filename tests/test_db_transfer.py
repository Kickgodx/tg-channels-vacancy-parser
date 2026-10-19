"""Дамп и импорт БД."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from backend.db.models import ExchangeRateRow, ParseRunRow, VacancyRow
from backend.db.transfer import (
    count_replace_losses,
    import_dump_file,
    load_dump_file,
    write_dump_file,
)


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


def seed_user_data(session) -> None:
    """2 оценки, 1 письмо и 3 LLM-задачи по вакансиям + задача импорта без вакансии."""
    from tests.conftest import new_user

    from backend.db import llm_job_repository as lj
    from backend.db import user_content_repository as uc

    user_id = new_user(session)
    resume = uc.create_resume(session, name="CV", text="Python", user_id=user_id)
    for vid in ("u-1", "u-2"):
        session.add(
            VacancyRow(
                id=vid,
                source_post_key=f"ch-{vid}",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                raw_text=vid,
                cleaned_text=vid,
            )
        )
        session.flush()
        uc.upsert_vacancy_match(
            session,
            resume_id=resume.id,
            vacancy_id=vid,
            overall_score=80,
            confidence=70,
            breakdown=None,
            explanation=None,
            matching_skills=None,
            missing_skills=None,
            strengths=None,
            risks=None,
            model="m",
        )
    uc.upsert_cover_letter(session, resume_id=resume.id, vacancy_id="u-1", text="Letter", tone="neutral", model="m")
    for kind, vid in (("match", "u-1"), ("match", "u-2"), ("cover_letter", "u-1")):
        lj.create_llm_job(session, user_id=user_id, kind=kind, resume_id=resume.id, vacancy_id=vid)
    lj.create_llm_job(session, user_id=user_id, kind="import", params={"texts": ["x"]})
    session.commit()


def test_count_replace_losses(db_session):
    seed_user_data(db_session)

    losses = count_replace_losses(db_session)

    assert (losses.vacancy_matches, losses.cover_letters, losses.llm_jobs) == (2, 1, 3)


def test_dump_skips_generated_search_tsv_and_reimports(db_session, tmp_path):
    from sqlalchemy import func, select

    _seed(db_session)
    dump_path = tmp_path / "dump.json"
    write_dump_file(db_session, dump_path)
    payload = load_dump_file(dump_path)
    assert all("search_tsv" not in row for row in payload["tables"]["vacancies"])

    db_session.query(VacancyRow).delete()
    db_session.commit()
    import_dump_file(db_session, dump_path, mode="merge")
    db_session.commit()
    # Postgres сам посчитал документ поиска для импортированной строки.
    found = db_session.scalar(
        select(func.count()).where(VacancyRow.search_tsv.op("@@")(func.plainto_tsquery("russian", "text")))
    )
    assert found == 1
