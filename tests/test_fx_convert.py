"""Конвертация зарплат в RUB."""

from datetime import UTC, datetime

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


def test_year_suffix_divides_by_12():
    assert parse_salary_currency("JPY/YEAR") == ("JPY", "year")
    assert monthly_salary_amount(9_000_000, "JPY/YEAR") == 750_000.0
    assert salary_amount_to_rub(9_000_000, "JPY/YEAR", {"JPY": 0.5}) == 375_000.0


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
        post_date=datetime(2026, 5, 1, tzinfo=UTC),
    )
    enrich_vacancy_salary_rub(v)
    assert v.detected_salary_avg_rub == 500000.0


def test_enrich_vacancies_salary_rub_one_network_call_on_cbr_failure(monkeypatch):
    """При недоступном ЦБ — один timeout, не N×timeout на каждую вакансию."""
    from tg_jobs_parser.fx import vacancy_rub as vr
    from tg_jobs_parser.fx.vacancy_rub import enrich_vacancies_salary_rub

    vr.clear_rates_cache()
    calls = {"n": 0}

    def _fail(d, timeout=20.0):
        calls["n"] += 1
        return None

    monkeypatch.setattr(vr, "_rates_for_date", _fail)
    vacancies = [
        Vacancy(
            id=f"v{i}",
            source_post_key=f"ch-{i}",
            source_channel="ch",
            source_url="https://t.me/s/ch",
            raw_text="t",
            cleaned_text="t",
            detected_salary_avg=1000.0 * (i + 1),
            salary_currency="USD",
            post_date=datetime(2026, 5, 1, tzinfo=UTC),
        )
        for i in range(10)
    ]
    enrich_vacancies_salary_rub(vacancies, request_timeout_seconds=20.0)
    assert calls["n"] == 1
    assert all(v.detected_salary_avg_rub is None for v in vacancies)


def test_enrich_vacancies_salary_rub_uses_preloaded_rates_without_network(monkeypatch):
    from datetime import date

    from tg_jobs_parser.fx import vacancy_rub as vr
    from tg_jobs_parser.fx.vacancy_rub import enrich_vacancies_salary_rub

    vr.clear_rates_cache()

    def _forbid(*_a, **_k):
        raise AssertionError("must not call CBR when rates_for_date provided")

    monkeypatch.setattr(vr, "_rates_for_date", _forbid)
    v = Vacancy(
        id="x",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="t",
        cleaned_text="t",
        detected_salary_avg=10.0,
        salary_currency="USD",
        post_date=datetime(2026, 5, 1, tzinfo=UTC),
    )
    enrich_vacancies_salary_rub(
        [v],
        rates_for_date={date(2026, 5, 10): {"RUB": 1.0, "USD": 90.0}},
    )
    assert v.detected_salary_avg_rub == 900.0


def test_hh_byr_is_converted_as_byn():
    """HH отдаёт белорусский рубль кодом BYR, курс ЦБ — под BYN."""
    assert parse_salary_currency("BYR") == ("BYN", "month")
    assert salary_amount_to_rub(1000.0, "BYR", {"BYN": 27.5}) == 27_500.0
