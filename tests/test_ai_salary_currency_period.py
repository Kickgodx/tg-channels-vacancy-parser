"""VacancyAIExtraction: сохранение периода в salary_currency."""

from __future__ import annotations

from jobdesk.ai.schema import VacancyAIExtraction


def test_preserves_usd_hour_suffix():
    e = VacancyAIExtraction(
        is_job_post=True,
        salary_min=25,
        salary_max=40,
        salary_currency="USD/HOUR",
    )
    assert e.salary_currency == "USD/HOUR"


def test_preserves_rub_hour_suffix():
    e = VacancyAIExtraction.model_validate(
        {
            "is_job_post": True,
            "salary_min": 2000,
            "salary_max": 3000,
            "salary_currency": "RUB/HOUR",
        }
    )
    assert e.salary_currency == "RUB/HOUR"


def test_monthly_rub_unchanged():
    e = VacancyAIExtraction(is_job_post=True, salary_currency="RUB")
    assert e.salary_currency == "RUB"


def test_preserves_jpy_year_suffix():
    e = VacancyAIExtraction(
        is_job_post=True,
        salary_min=6_000_000,
        salary_max=12_000_000,
        salary_currency="JPY/YEAR",
    )
    assert e.salary_currency == "JPY/YEAR"
