"""Конвертация зарплат в RUB."""

from datetime import datetime, timezone

from tg_jobs_parser.fx.convert import effective_salary_amount, salary_amount_to_rub
from tg_jobs_parser.fx.vacancy_rub import enrich_vacancy_salary_rub
from tg_jobs_parser.models.vacancy import Vacancy


def test_salary_amount_to_rub_usd():
    rates = {"RUB": 1.0, "USD": 90.0}
    assert salary_amount_to_rub(1000, "USD", rates) == 90000.0


def test_salary_amount_to_rub_usdt_uses_usd_rate():
    rates = {"RUB": 1.0, "USD": 90.0}
    assert salary_amount_to_rub(1000, "USDT", rates) == 90000.0


def test_salary_amount_to_rub_missing_currency_default_rub():
    rates = {"RUB": 1.0, "USD": 90.0}
    assert salary_amount_to_rub(200000, None, rates, default_currency_if_missing="RUB") == 200000.0


def test_effective_salary_amount_range():
    assert effective_salary_amount(salary_min=100, salary_max=200) == 150.0


def test_enrich_vacancy_salary_rub_with_mocked_rates(monkeypatch):
    from tg_jobs_parser.fx import vacancy_rub as vr

    vr.clear_rates_cache()
    monkeypatch.setattr(
        vr,
        "_rates_for_date",
        lambda d, timeout=20.0: {"RUB": 1.0, "USD": 100.0},
    )
    v = Vacancy(
        id="x",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="t",
        cleaned_text="t",
        detected_salary_avg=5000,
        salary_currency="USD",
        post_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    enrich_vacancy_salary_rub(v)
    assert v.detected_salary_avg_rub == 500000.0
