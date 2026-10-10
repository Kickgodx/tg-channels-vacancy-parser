"""Метрики с курсами в БД."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db import fx_repository as fx_repo
from backend.db.base import Base
from backend.db.repository import upsert_vacancies
from backend.db.vacancy_metrics import aggregate_vacancy_metrics
from backend.schemas import VacancyQuery
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.metrics import SalaryDisplayMeta
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'fx.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_prepare_fx_uses_latest_rates_from_db_without_network(db_session, monkeypatch, tmp_path):
    """Метрики/аналитика не должны ходить в ЦБ — только последний снимок из БД."""
    cfg = tmp_path / "fx_on.yaml"
    cfg.write_text(
        """
telegram:
  telethon_session_file: sessions/tg_jobs.session
sources:
  channels: [test_channel]
search:
  date_from: week_ago
  date_to: today
filters:
  specializations: [Python]
  exclude_keywords: []
  work_formats: [any]
ai:
  enabled: false
fx:
  enabled: true
  provider: cbr
  display_currency: RUB
  sync_days_on_startup: 14
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    from backend.settings import get_settings

    get_settings.cache_clear()

    d = date(2026, 5, 10)
    fx_repo.upsert_rates(db_session, d, {"RUB": 1.0, "USD": 90.0})
    db_session.commit()

    def _forbid_network(*_a, **_k):
        raise AssertionError("analytics must not fetch FX rates from network")

    monkeypatch.setattr(fx_repo, "sync_recent_days", _forbid_network)
    monkeypatch.setattr(fx_repo, "ensure_latest_rates", _forbid_network)
    monkeypatch.setattr(fx_repo, "ensure_rates", _forbid_network)

    from backend.db.vacancy_metrics import _prepare_fx

    meta = _prepare_fx(db_session)
    assert meta.rates_as_of == "2026-05-10"
    assert meta.display_currency == "RUB"
    assert meta.rates_source == "cbr"
    get_settings.cache_clear()


def test_metrics_avg_salary_in_rub(db_session, monkeypatch):
    monkeypatch.setattr(
        "backend.db.vacancy_metrics._prepare_fx",
        lambda _session: SalaryDisplayMeta(display_currency="RUB", rates_as_of="2026-05-10", rates_source="cbr"),
    )
    d = date(2026, 5, 10)
    fx_cfg = FxConfig(enabled=True)
    fx_repo.upsert_rates(db_session, d, {"RUB": 1.0, "USD": 100.0})
    db_session.commit()

    run_id = uuid.uuid4()
    t_rub = "Python dev 200k rub"
    t_usd = "Python dev 5k usd"
    v1 = Vacancy(
        id=vacancy_content_id(t_rub),
        source_post_key="a-1",
        source_channel="a",
        source_url="https://t.me/s/a",
        raw_text=t_rub,
        cleaned_text=t_rub,
        detected_salary_avg=200000.0,
        salary_currency="RUB",
        post_date=datetime(2026, 5, 10, tzinfo=UTC),
        detected_salary_avg_rub=200000.0,
    )
    v2 = Vacancy(
        id=vacancy_content_id(t_usd),
        source_post_key="a-2",
        source_channel="a",
        source_url="https://t.me/s/a",
        raw_text=t_usd,
        cleaned_text=t_usd,
        detected_salary_avg=5000.0,
        salary_currency="USD",
        post_date=datetime(2026, 5, 10, tzinfo=UTC),
        detected_salary_avg_rub=500000.0,
    )
    upsert_vacancies(db_session, [v1, v2], parse_run_id=run_id, fx_config=fx_cfg)
    db_session.commit()

    metrics = aggregate_vacancy_metrics(db_session, VacancyQuery())
    # (200k + 500k) / 2 = 350k в одном грейде
    assert metrics.salary_by_grade["unknown"].mean == pytest.approx(350000.0, rel=0.01)
