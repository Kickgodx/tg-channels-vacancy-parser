"""Конвертация зарплат в RUB."""

from datetime import datetime, timezone

from tg_jobs_parser.fx.convert import (
    HOURS_PER_MONTH,
    effective_salary_amount,
    is_ruble_currency,
    monthly_salary_amount,
    normalize_currency,
    parse_salary_currency,
    salary_amount_to_rub,
)
from tg_jobs_parser.fx.vacancy_rub import enrich_vacancy_salary_rub
from tg_jobs_parser.models.vacancy import Vacancy


def test_salary_amount_to_rub_usd():
    rates = {"RUB": 1.0, "USD": 90.0}
    assert salary_amount_to_rub(1000, "USD", rates) == 90000.0


def test_rur_equals_rub():
    assert normalize_currency("RUR") == "RUB"
    assert is_ruble_currency("RUR")
    assert salary_amount_to_rub(125000, "RUR", {"RUB": 1.0, "USD": 90.0}) == 125000.0


def test_rub_hourly_monthly_conversion():
    assert monthly_salary_amount(3000, "RUB/HOUR") == 3000 * HOURS_PER_MONTH
    assert salary_amount_to_rub(1000, "RUB/HOUR", {"RUB": 1.0}) == 1000 * HOURS_PER_MONTH


def test_rub_per_hour_to_monthly_rub():
    assert parse_salary_currency("RUB/HOUR") == ("RUB", "hour")
    assert normalize_currency("RUB/HOUR") == "RUB"
    assert monthly_salary_amount(700, "RUB/HOUR") == 700 * HOURS_PER_MONTH
    assert salary_amount_to_rub(700, "RUB/HOUR", {"RUB": 1.0}) == 700 * HOURS_PER_MONTH


def test_dollar_sign_equals_usd():
    assert normalize_currency("$") == "USD"
    assert salary_amount_to_rub(2100, "$", {"RUB": 1.0, "USD": 90.0}) == 189000.0


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
