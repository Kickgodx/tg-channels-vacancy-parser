"""Расчёт ``detected_salary_avg_rub`` для модели Vacancy (без привязки к backend)."""

from __future__ import annotations

import logging
from datetime import date

from tg_jobs_parser.fx.cbr import fetch_cbr_rates
from tg_jobs_parser.fx.convert import (
    effective_salary_amount,
    rate_date_for_live_display,
    salary_amount_to_rub,
)
from tg_jobs_parser.models.vacancy import Vacancy


def effective_salary_amount_for_vacancy(v: Vacancy) -> float | None:
    return effective_salary_amount(
        salary_avg=v.detected_salary_avg,
        salary_min=v.detected_salary_min,
        salary_max=v.detected_salary_max,
    )

_LOG = logging.getLogger(__name__)

_rates_cache: dict[date, dict[str, float]] = {}


def clear_rates_cache() -> None:
    _rates_cache.clear()


def _rates_for_date(rate_date: date, *, timeout: float) -> dict[str, float] | None:
    if rate_date in _rates_cache:
        return _rates_cache[rate_date]
    try:
        actual_date, rates = fetch_cbr_rates(rate_date, timeout=timeout)
        _rates_cache[actual_date] = rates
        if actual_date != rate_date:
            _rates_cache[rate_date] = rates
        return rates
    except Exception as e:
        _LOG.warning("Не удалось загрузить курс ЦБ на %s: %s", rate_date, e)
        return None


def _live_rates(*, timeout: float) -> dict[str, float] | None:
    """Последний курс: сегодня (daily_json ЦБ), с кэшем в памяти на время прогона."""
    today = rate_date_for_live_display()
    rates = _rates_for_date(today, timeout=timeout)
    if rates:
        return rates
    if not _rates_cache:
        return None
    latest = max(_rates_cache)
    return _rates_cache[latest]


def enrich_vacancy_salary_rub(
    v: Vacancy,
    *,
    default_currency_if_missing: str = "RUB",
    request_timeout_seconds: float = 20.0,
    rates_for_date: dict[date, dict[str, float]] | None = None,
) -> None:
    """Заполняет ``detected_salary_avg_rub`` по последнему курсу (сегодня), не по дате поста."""
    amount = effective_salary_amount_for_vacancy(v)
    if amount is None:
        v.detected_salary_avg_rub = None
        return
    rates: dict[str, float] | None = None
    if rates_for_date:
        if rates_for_date:
            latest = max(rates_for_date)
            rates = rates_for_date[latest]
    if rates is None:
        rates = _live_rates(timeout=request_timeout_seconds)
    if rates is None:
        v.detected_salary_avg_rub = None
        return
    v.detected_salary_avg_rub = salary_amount_to_rub(
        amount,
        v.salary_currency,
        rates,
        default_currency_if_missing=default_currency_if_missing,
    )


def enrich_vacancies_salary_rub(
    vacancies: list[Vacancy],
    *,
    default_currency_if_missing: str = "RUB",
    request_timeout_seconds: float = 20.0,
) -> None:
    """Один снимок курса на сегодня, затем enrich всех вакансий."""
    today = rate_date_for_live_display()
    shared: dict[date, dict[str, float]] = {}
    r = _rates_for_date(today, timeout=request_timeout_seconds)
    if r is not None:
        shared[today] = r
    elif _rates_cache:
        shared[max(_rates_cache)] = _rates_cache[max(_rates_cache)]
    for v in vacancies:
        enrich_vacancy_salary_rub(
            v,
            default_currency_if_missing=default_currency_if_missing,
            request_timeout_seconds=request_timeout_seconds,
            rates_for_date=shared,
        )
