"""Пакетный UPSERT вакансий и потолок глубины OFFSET-пагинации."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker
from tests.conftest import new_parse_run

from backend.db import vacancy_repository as vr
from backend.db.models import VacancyRow
from backend.db.session import get_engine
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.vacancy import Vacancy

FX_OFF = FxConfig(enabled=False)


@pytest.fixture
def db(pg_db):
    engine = get_engine()
    inserts: list[int] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO VACANCIES"):
            inserts.append(1)

    session = sessionmaker(bind=engine)()
    session.info["inserts"] = inserts
    yield session
    session.close()


def _v(i: int, **kw) -> Vacancy:
    return Vacancy(
        id=f"v{i:04d}",
        source_post_key=f"chan-{i}",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url=f"https://t.me/chan/{i}",
        post_date=kw.pop("post_date", datetime(2026, 9, 1, tzinfo=UTC)),
        raw_text=f"text {i}",
        cleaned_text=f"text {i}",
        detected_specializations=["Python"],
        detected_grade=kw.pop("detected_grade", "middle"),
        **kw,
    )


def test_batch_insert_uses_one_statement_per_chunk(db):
    n = vr._UPSERT_CHUNK * 2 + 50
    stats = vr.upsert_vacancies(db, [_v(i) for i in range(n)], parse_run_id=None, fx_config=FX_OFF)
    db.commit()

    assert (stats.inserted, stats.updated_existing) == (n, 0)
    assert len(db.info["inserts"]) == 3  # 200 + 200 + 50, а не n отдельных INSERT
    assert db.scalar(select(VacancyRow).where(VacancyRow.id == f"v{n - 1:04d}").with_only_columns(VacancyRow.id))


def test_existing_rows_take_new_extraction_and_metadata(db):
    first_run, second_run = new_parse_run(db), new_parse_run(db)
    old = datetime(2026, 9, 1, tzinfo=UTC)
    vr.upsert_vacancies(db, [_v(1, post_date=old), _v(2, post_date=old)], parse_run_id=first_run, fx_config=FX_OFF)
    db.execute(VacancyRow.__table__.update().values(archived_at=datetime(2026, 10, 1, tzinfo=UTC)))
    db.commit()

    reposted = datetime(2026, 10, 10, tzinfo=UTC)
    batch = [
        _v(1, post_date=reposted, detected_grade="senior", ai_notes="new notes", extraction_source="ai"),
        _v(2, post_date=old),  # та же дата — архив не снимается
        _v(3),  # новая
    ]
    stats = vr.upsert_vacancies(db, batch, parse_run_id=second_run, fx_config=FX_OFF)
    db.commit()

    assert (stats.inserted, stats.updated_existing) == (1, 2)
    rows = {r.id: r for r in db.scalars(select(VacancyRow))}
    r1, r2 = rows["v0001"], rows["v0002"]
    assert r1.detected_grade == "senior"  # LLM-разбор поверх rules заменяет поля (C1)
    assert (r1.ai_notes, r1.extraction_source) == ("new notes", "ai")
    assert r1.last_parse_run_id == second_run
    assert r1.post_date.replace(tzinfo=UTC) == reposted and r1.archived_at is None
    assert r2.archived_at is not None and r2.post_date.replace(tzinfo=UTC) == old
    assert "v0003" in rows


def test_manual_import_keeps_run_binding(db):
    run = new_parse_run(db)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=run, fx_config=FX_OFF)
    vr.upsert_vacancies(db, [_v(1)], parse_run_id=None, fx_config=FX_OFF)
    db.commit()
    assert db.get(VacancyRow, "v0001").last_parse_run_id == run


def test_duplicate_id_in_batch_is_written_once(db):
    stats = vr.upsert_vacancies(db, [_v(1), _v(1, ai_notes="last")], parse_run_id=None, fx_config=FX_OFF)
    db.commit()
    assert stats.inserted == 1
    assert db.get(VacancyRow, "v0001").ai_notes == "last"


def test_list_depth_is_capped(api_client):
    from backend.api.params import MAX_LIST_DEPTH

    # Первый пользователь — admin: /runs/* admin-only.
    token = api_client.post("/api/v1/auth/register", json={"email": "d@example.com", "password": "secret1"})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    last_page = MAX_LIST_DEPTH // 50
    assert api_client.get(f"/api/v1/vacancies?page={last_page}&page_size=50", headers=headers).status_code == 200
    too_deep = api_client.get(f"/api/v1/vacancies?page={last_page + 1}&page_size=50", headers=headers)
    assert too_deep.status_code == 422
    assert too_deep.json()["detail"]["code"] == "VALIDATION"
    run_deep = api_client.get(f"/api/v1/runs/{uuid.uuid4()}/vacancies?page=21&page_size=500", headers=headers)
    assert run_deep.status_code == 422
