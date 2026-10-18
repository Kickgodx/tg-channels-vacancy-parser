"""Backfill зарплат в RUB без запроса к ЦБ."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from tests.conftest import new_parse_run

from backend.db import fx_repository as fx_repo
from backend.db.models import VacancyRow
from backend.db.repository import upsert_vacancies
from backend.fx.backfill import backfill_vacancies_salary_rub
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.utils.hashing import vacancy_content_id


def test_ensure_latest_rates_prefers_db_without_network(db_session, monkeypatch):
    d = date(2026, 5, 19)
    fx_cfg = FxConfig(enabled=True)
    fx_repo.upsert_rates(db_session, d, {"RUB": 1.0, "USD": 80.0})
    db_session.commit()

    def _forbid(*_a, **_k):
        raise AssertionError("ensure_latest_rates(prefer_db) must not call CBR")

    monkeypatch.setattr(fx_repo, "ensure_rates", _forbid)
    monkeypatch.setattr("tg_jobs_parser.fx.cbr.fetch_cbr_rates", _forbid)

    rate_date, rates = fx_repo.ensure_latest_rates(db_session, fx_cfg)
    assert rate_date == d
    assert rates["USD"] == 80.0


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
        post_date=datetime(2026, 5, 1, tzinfo=UTC),
        detected_salary_avg_rub=None,
    )
    # UPSERT сам подтягивает «последний курс»: без подмены он сходил бы в ЦБ
    # и положил в БД сегодняшний курс, который перебил бы засеянный выше.
    monkeypatch.setattr(fx_repo, "ensure_latest_rates", lambda *_a, **_k: (d, {"RUB": 1.0, "USD": 80.0}))
    upsert_vacancies(db_session, [v], parse_run_id=new_parse_run(db_session), fx_config=fx_cfg)
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


def test_backfill_converts_annual_jpy_to_monthly(db_session, monkeypatch):
    d = date(2026, 5, 19)
    fx_cfg = FxConfig(enabled=True)
    fx_repo.upsert_rates(db_session, d, {"RUB": 1.0, "JPY": 0.54})
    db_session.commit()

    text = "SRE | 6M - 12M JPY Annually | Tokyo"
    v = Vacancy(
        id=vacancy_content_id(text),
        source_post_key="ch-jpy-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=6_000_000.0,
        detected_salary_max=12_000_000.0,
        detected_salary_avg=9_000_000.0,
        salary_currency="JPY",
        post_date=datetime(2026, 5, 1, tzinfo=UTC),
        detected_salary_avg_rub=4_855_041.0,
    )
    monkeypatch.setattr(fx_repo, "ensure_latest_rates", lambda *_a, **_k: (d, {"RUB": 1.0, "JPY": 0.54}))
    upsert_vacancies(db_session, [v], parse_run_id=new_parse_run(db_session), fx_config=fx_cfg)
    db_session.commit()

    monkeypatch.setattr(
        fx_repo,
        "ensure_latest_rates",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("ensure_latest_rates must not be called when db_rates_only=True")
        ),
    )
    monkeypatch.setattr(
        fx_repo,
        "ensure_rates",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ensure_rates must not be called")),
    )

    stats = backfill_vacancies_salary_rub(db_session, fx_cfg, db_rates_only=True)
    db_session.commit()

    row = db_session.scalar(select(VacancyRow).where(VacancyRow.id == v.id))
    assert stats.updated == 1
    assert row is not None
    assert row.salary_currency == "JPY"
    assert row.detected_salary_min == pytest.approx(500_000.0)
    assert row.detected_salary_max == pytest.approx(1_000_000.0)
    assert row.detected_salary_avg == pytest.approx(750_000.0)
    assert row.detected_salary_avg_rub == pytest.approx(750_000.0 * 0.54)

    # Идемпотентность: повторный backfill не делит ещё раз.
    stats2 = backfill_vacancies_salary_rub(db_session, fx_cfg, db_rates_only=True)
    db_session.commit()
    row2 = db_session.scalar(select(VacancyRow).where(VacancyRow.id == v.id))
    assert stats2.updated == 1
    assert row2 is not None
    assert row2.detected_salary_avg == pytest.approx(750_000.0)
    assert row2.detected_salary_avg_rub == pytest.approx(750_000.0 * 0.54)


def _no_currency_vacancy(amount: float) -> Vacancy:
    text = f"Python developer, зарплата {amount:.0f}"
    return Vacancy(
        id=vacancy_content_id(text),
        source_post_key="ch-nocur",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text=text,
        cleaned_text=text,
        detected_salary_avg=amount,
        salary_currency=None,
        post_date=datetime(2026, 5, 1, tzinfo=UTC),
    )


def _saved(db_session, v: Vacancy, fx_cfg: FxConfig) -> VacancyRow:
    upsert_vacancies(db_session, [v], parse_run_id=new_parse_run(db_session), fx_config=fx_cfg)
    db_session.commit()
    row = db_session.scalar(select(VacancyRow).where(VacancyRow.id == v.id))
    assert row is not None
    return row


def test_salary_without_currency_counts_as_default_rub(db_session, monkeypatch):
    def _forbid(*_a, **_k):
        raise AssertionError("рублю курс не нужен")

    monkeypatch.setattr(fx_repo, "ensure_latest_rates", _forbid)

    row = _saved(db_session, _no_currency_vacancy(200_000), FxConfig(enabled=False))

    assert row.detected_salary_avg_rub == pytest.approx(200_000.0)
    assert row.salary_currency is None


def test_salary_without_currency_uses_non_ruble_default(db_session, monkeypatch):
    d = date(2026, 5, 19)
    monkeypatch.setattr(fx_repo, "ensure_latest_rates", lambda *_a, **_k: (d, {"RUB": 1.0, "USD": 80.0}))

    row = _saved(db_session, _no_currency_vacancy(2_000), FxConfig(enabled=True, default_currency_if_missing="USD"))

    assert row.detected_salary_avg_rub == pytest.approx(160_000.0)
    assert row.salary_currency is None


def test_empty_default_currency_leaves_no_rub_sum(db_session, monkeypatch):
    d = date(2026, 5, 19)
    monkeypatch.setattr(fx_repo, "ensure_latest_rates", lambda *_a, **_k: (d, {"RUB": 1.0, "USD": 80.0}))

    row = _saved(db_session, _no_currency_vacancy(200_000), FxConfig(enabled=True, default_currency_if_missing=""))

    assert row.detected_salary_avg_rub is None


def test_backfill_fills_salary_without_currency(db_session, monkeypatch):
    d = date(2026, 5, 19)
    fx_repo.upsert_rates(db_session, d, {"RUB": 1.0, "USD": 80.0})
    db_session.commit()
    monkeypatch.setattr(fx_repo, "ensure_latest_rates", lambda *_a, **_k: (d, {"RUB": 1.0, "USD": 80.0}))
    # Записана до исправления: рублёвой суммы нет.
    row = _saved(db_session, _no_currency_vacancy(150_000), FxConfig(enabled=True, default_currency_if_missing=""))
    assert row.detected_salary_avg_rub is None

    stats = backfill_vacancies_salary_rub(db_session, FxConfig(enabled=True), db_rates_only=True)
    db_session.commit()
    db_session.refresh(row)

    assert stats.updated == 1
    assert row.detected_salary_avg_rub == pytest.approx(150_000.0)


def _usd_vacancy(i: int, amount: float = 1_000) -> Vacancy:
    text = f"Go developer #{i}, {amount:.0f} USD"
    return Vacancy(
        id=vacancy_content_id(text),
        source_post_key=f"ch-usd-{i}",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text=text,
        cleaned_text=text,
        detected_salary_avg=amount,
        salary_currency="USD",
        post_date=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_backfill_includes_archived_vacancies(db_session):
    fx_cfg = FxConfig(enabled=True)
    fx_repo.upsert_rates(db_session, date(2026, 5, 19), {"RUB": 1.0, "USD": 80.0})
    db_session.commit()
    v = _usd_vacancy(1)
    row = _saved(db_session, v, fx_cfg)
    assert row.detected_salary_avg_rub == pytest.approx(80_000.0)
    row.archived_at = datetime(2026, 6, 1, tzinfo=UTC)
    fx_repo.upsert_rates(db_session, date(2026, 5, 20), {"RUB": 1.0, "USD": 90.0})
    db_session.commit()

    stats = backfill_vacancies_salary_rub(db_session, fx_cfg, db_rates_only=True)
    db_session.commit()
    db_session.refresh(row)

    assert stats.processed == 1
    assert row.detected_salary_avg_rub == pytest.approx(90_000.0)


def test_cbr_down_on_empty_db_is_asked_once_per_batch(db_session, monkeypatch):
    calls = []

    def _cbr_down(*_a, **_k):
        calls.append(1)
        raise ConnectionError("cbr down")

    monkeypatch.setattr(fx_repo, "fetch_cbr_rates", _cbr_down)
    batch = [_usd_vacancy(i) for i in range(30)]

    upsert_vacancies(db_session, batch, parse_run_id=new_parse_run(db_session), fx_config=FxConfig(enabled=True))
    db_session.commit()

    assert len(calls) == 1
    rows = db_session.scalars(select(VacancyRow)).all()
    assert len(rows) == 30
    assert all(r.detected_salary_avg_rub is None for r in rows)
