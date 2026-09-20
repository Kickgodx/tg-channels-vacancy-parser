"""Backfill зарплат в RUB без запроса к ЦБ."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.db import fx_repository as fx_repo
from backend.db.base import Base
from backend.db.models import VacancyRow
from backend.db.repository import upsert_vacancies
from backend.fx.backfill import backfill_vacancies_salary_rub
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'backfill.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_backfill_db_rates_only_uses_latest_in_db(db_session, monkeypatch):
    d = date(2026, 5, 19)
    fx_cfg = FxConfig(enabled=True)
    fx_repo.upsert_rates(db_session, d, {"RUB": 1.0, "USD": 80.0})
    db_session.commit()

    text = "QA 1000 usd remote"
    v = Vacancy(
        id=vacancy_content_id(text),
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text=text,
        cleaned_text=text,
        detected_salary_avg=1000.0,
        salary_currency="USD",
        post_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
        detected_salary_avg_rub=None,
    )
    # UPSERT сам подтягивает «последний курс»: без подмены он сходил бы в ЦБ
    # и положил в БД сегодняшний курс, который перебил бы засеянный выше.
    monkeypatch.setattr(
        fx_repo, "ensure_latest_rates", lambda *_a, **_k: (d, {"RUB": 1.0, "USD": 80.0})
    )
    upsert_vacancies(db_session, [v], parse_run_id=uuid.uuid4(), fx_config=fx_cfg)
    db_session.commit()

    def _fail_ensure(*_a, **_k):
        raise AssertionError("ensure_latest_rates must not be called when db_rates_only=True")

    monkeypatch.setattr(fx_repo, "ensure_latest_rates", _fail_ensure)
    monkeypatch.setattr(fx_repo, "ensure_rates", _fail_ensure)

    stats = backfill_vacancies_salary_rub(db_session, fx_cfg, db_rates_only=True)
    db_session.commit()

    row = db_session.scalar(select(VacancyRow).where(VacancyRow.id == v.id))
    assert stats.updated == 1
    assert row is not None
    assert row.detected_salary_avg_rub == pytest.approx(80_000.0)
