from datetime import UTC, datetime

import pytest

from tg_jobs_parser.extraction.salary_period import (
    annual_markers_in_text,
    apply_hourly_currency_from_text,
    hourly_markers_in_text,
    normalize_annual_salary_to_monthly,
    sanitize_salary_amounts,
    sanitize_salary_currency_period,
)
from tg_jobs_parser.models.vacancy import Vacancy


def _vacancy(**kwargs) -> Vacancy:
    base = dict(
        id="ch-1",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="text",
        cleaned_text="text",
        parsed_at=datetime.now(UTC),
    )
    base.update(kwargs)
    return Vacancy(**base)


def test_v_chasti_is_not_hourly_marker():
    text = "критерии перевода в регресс) в части контроля качества"
    assert hourly_markers_in_text(text) is False


def test_sanitize_strips_false_hourly_from_ai():
    text = "Вилка: от 200 тр до 300 тр"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        salary_currency="RUB/HOUR",
        detected_salary_avg=250_000,
    )
    sanitize_salary_currency_period(v)
    assert v.salary_currency == "RUB"


def test_annual_markers_jpy_annually():
    text = "6M - 12M JPY Annually Japan | Tokyo Office"
    assert annual_markers_in_text(text) is True


def test_normalize_annual_jpy_plain_currency_divides_by_12():
    text = "SRE | 6M - 12M JPY Annually | Tokyo"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=6_000_000.0,
        detected_salary_max=12_000_000.0,
        detected_salary_avg=9_000_000.0,
        salary_currency="JPY",
    )
    assert normalize_annual_salary_to_monthly(v) is True
    assert v.salary_currency == "JPY"
    assert v.detected_salary_min == 500_000.0
    assert v.detected_salary_max == 1_000_000.0
    assert v.detected_salary_avg == 750_000.0


def test_normalize_annual_year_suffix_always_converts():
    text = "Salary 120000 USD/year remote"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_avg=120_000.0,
        salary_currency="USD/YEAR",
    )
    assert normalize_annual_salary_to_monthly(v) is True
    assert v.salary_currency == "USD"
    assert v.detected_salary_avg == 10_000.0


def test_normalize_annual_idempotent_after_monthly():
    text = "6M - 12M JPY Annually"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=500_000.0,
        detected_salary_max=1_000_000.0,
        detected_salary_avg=750_000.0,
        salary_currency="JPY",
    )
    assert normalize_annual_salary_to_monthly(v) is False
    assert v.detected_salary_avg == 750_000.0


def test_normalize_skips_hourly():
    text = "$50 / hour Annually mentioned by mistake"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_avg=50.0,
        salary_currency="USD/HOUR",
    )
    assert normalize_annual_salary_to_monthly(v) is False
    assert v.salary_currency == "USD/HOUR"
    assert v.detected_salary_avg == 50.0


def test_experience_year_s_is_not_annual_marker():
    text = "Network Architect | 6 year(s) | Senior | IC | 5000 CAD monthly remote"
    assert annual_markers_in_text(text) is False


def test_normalize_cad_annually_high_band():
    """70k–100k CAD Annually — выше зоны неоднозначности, код делит."""
    text = "Network Architect | 6 year(s) | 70k - 100k CAD Annually | Canada Remote"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=70_000.0,
        detected_salary_max=100_000.0,
        detected_salary_avg=85_000.0,
        salary_currency="CAD",
    )
    assert normalize_annual_salary_to_monthly(v) is True
    assert v.detected_salary_avg == pytest.approx(85_000 / 12)
    assert v.salary_currency == "CAD"
    assert normalize_annual_salary_to_monthly(v) is False


def test_normalize_leaves_low_eur_annually_to_llm():
    """26k–39k EUR Annually пересекается с месячным после /12 у высоких TC — не трогаем."""
    text = "QA Engineer II | 26k - 39k EUR Annually | Slovakia Remote"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=26_000.0,
        detected_salary_max=39_000.0,
        detected_salary_avg=32_500.0,
        salary_currency="EUR",
    )
    assert normalize_annual_salary_to_monthly(v) is False
    assert v.detected_salary_avg == 32_500.0


def test_normalize_leaves_low_usd_annually_to_llm():
    text = "Senior Marketing Data Analyst | 30k - 47.5k USD Annually | Lisbon"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=30_000.0,
        detected_salary_max=47_500.0,
        detected_salary_avg=38_750.0,
        salary_currency="USD",
    )
    assert normalize_annual_salary_to_monthly(v) is False


def test_normalize_does_not_divide_director_monthly_with_annually_in_text():
    """Уже месячные ~35k USD + Annually в тексте — не делить повторно."""
    text = "QA Director | $420k Annually | Apple"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=20_833.0,
        detected_salary_max=37_500.0,
        detected_salary_avg=35_000.0,
        salary_currency="USD",
    )
    assert normalize_annual_salary_to_monthly(v) is False
    assert v.detected_salary_avg == 35_000.0


def test_normalize_leaves_usd_k_without_annually_to_llm():
    text = (
        "AI, frontend, full-stack engineers. $125k–225k + equity, relocation to London. "
        "Помогают с визой, платят $125k–225k + equity."
    )
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=125_000.0,
        detected_salary_max=225_000.0,
        detected_salary_avg=175_000.0,
        salary_currency="USD",
    )
    assert annual_markers_in_text(text) is False
    assert normalize_annual_salary_to_monthly(v) is False


def test_normalize_usd_annually_high_band():
    text = "Staff Engineer | $125k–225k Annually + equity | London"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=125_000.0,
        detected_salary_max=225_000.0,
        detected_salary_avg=175_000.0,
        salary_currency="USD",
    )
    assert normalize_annual_salary_to_monthly(v) is True
    assert v.detected_salary_avg == pytest.approx(175_000 / 12)
    assert normalize_annual_salary_to_monthly(v) is False


def test_sanitize_amounts_swaps_min_greater_than_max():
    v = _vacancy(
        detected_salary_min=250_000, detected_salary_max=200_000, detected_salary_avg=225_000, salary_currency="RUB"
    )
    assert sanitize_salary_amounts(v) is True
    assert (v.detected_salary_min, v.detected_salary_max) == (200_000, 250_000)
    assert v.detected_salary_avg == 225_000


def test_sanitize_amounts_recomputes_avg_from_min_max():
    v = _vacancy(
        detected_salary_min=10_000, detected_salary_max=20_000, detected_salary_avg=1_000, salary_currency="USD"
    )
    sanitize_salary_amounts(v)
    assert v.detected_salary_avg == 15_000


def test_sanitize_amounts_clears_small_amount_without_currency():
    v = _vacancy(detected_salary_min=2500, detected_salary_avg=2500, salary_currency=None)
    assert sanitize_salary_amounts(v) is True
    assert v.detected_salary_min is None and v.detected_salary_avg is None


def test_sanitize_amounts_keeps_plausible_amount_without_currency():
    v = _vacancy(detected_salary_min=110_000, detected_salary_avg=110_000, salary_currency=None)
    assert sanitize_salary_amounts(v) is False
    assert v.detected_salary_avg == 110_000


def test_sanitize_amounts_clears_tiny_monthly_rub():
    v = _vacancy(detected_salary_min=180, detected_salary_avg=180, salary_currency="RUB")
    sanitize_salary_amounts(v)
    assert v.detected_salary_avg is None and v.salary_currency is None


def test_sanitize_amounts_small_rub_with_hourly_marker_becomes_hourly():
    text = "Вилка обсуждается. Оплата 400 - 550/ч, парт-тайм 2–4 часа в день"
    v = _vacancy(
        raw_text=text,
        cleaned_text=text,
        detected_salary_min=400,
        detected_salary_max=550,
        detected_salary_avg=475,
        salary_currency="RUB",
    )
    sanitize_salary_amounts(v)
    assert v.salary_currency == "RUB/HOUR"
    assert v.detected_salary_avg == 475


def test_sanitize_amounts_leaves_hourly_and_foreign_small_amounts():
    v = _vacancy(detected_salary_min=50, detected_salary_avg=50, salary_currency="USD/HOUR")
    assert sanitize_salary_amounts(v) is False
    v = _vacancy(detected_salary_min=2500, detected_salary_avg=2500, salary_currency="EUR")
    assert sanitize_salary_amounts(v) is False


def test_sanitize_amounts_keeps_avg_only_shape():
    v = _vacancy(detected_salary_avg=150_000, salary_currency="RUB")
    assert sanitize_salary_amounts(v) is False
    assert v.detected_salary_min is None


def test_hourly_and_monthly_options_keep_monthly_amount():
    """Регресс: «от $20/час или от $2000/мес» с месячной суммой не становится USD/HOUR (28 млн ₽)."""
    text = "Ставка: от $20/час или от $2000/мес (USDT, ИП)"
    v = _vacancy(
        raw_text=text, cleaned_text=text, detected_salary_min=2000, detected_salary_avg=2000, salary_currency="USD"
    )
    apply_hourly_currency_from_text(v)
    sanitize_salary_amounts(v)
    assert v.salary_currency == "USD"


def test_large_amount_is_not_marked_hourly_by_text():
    text = "Оплата 2000 руб/час на испытательном, потом оклад"
    v = _vacancy(raw_text=text, cleaned_text=text, detected_salary_avg=150_000, salary_currency="RUB")
    apply_hourly_currency_from_text(v)
    assert v.salary_currency == "RUB"


def test_sanitize_amounts_keeps_small_amount_when_missing_currency_is_not_rub():
    """При ``fx.default_currency_if_missing: USD`` 2 000 без валюты — нормальная месячная зарплата."""
    v = _vacancy(detected_salary_min=2000, detected_salary_avg=2000, salary_currency=None)
    assert sanitize_salary_amounts(v, default_currency_if_missing="USD") is False
    assert v.detected_salary_avg == 2000
