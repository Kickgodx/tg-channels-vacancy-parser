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
    """Заполняет ``detected_salary_avg_rub`` по последнему курсу (сегодня), не по дате поста.

    ``rates_for_date``:
    - ``None`` — один live-запрос ЦБ (одиночный enrich);
    - ``{}`` — сеть уже пробовали, курсов нет (не ходить в ЦБ снова);
    - непустой dict — взять последний снимок из него.
    """
    amount = effective_salary_amount_for_vacancy(v)
    if amount is None:
        v.detected_salary_avg_rub = None
        return
    rates: dict[str, float] | None = None
    if rates_for_date is not None:
        if rates_for_date:
            rates = rates_for_date[max(rates_for_date)]
        # пустой dict: явный «без курсов», без повторного HTTP
    else:
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
    rates_for_date: dict[date, dict[str, float]] | None = None,
) -> None:
    """Один снимок курса, затем enrich всех вакансий (без N×HTTP при фейле).

    ``rates_for_date`` — готовые курсы (например из БД); если передан, ЦБ не вызываем.
    Если ``None`` — один запрос ЦБ на сегодня (CLI / холодный путь).
    """
    if rates_for_date is not None:
        shared = rates_for_date
    else:
        today = rate_date_for_live_display()
        shared = {}
        r = _rates_for_date(today, timeout=request_timeout_seconds)
        if r is not None:
            shared[today] = r
        elif _rates_cache:
            shared[max(_rates_cache)] = _rates_cache[max(_rates_cache)]
    # Всегда передаём dict (возможно пустой): иначе при фейле ЦБ каждая вакансия
    # снова била бы сеть на request_timeout_seconds (~20с × N).
    for v in vacancies:
        enrich_vacancy_salary_rub(
            v,
            default_currency_if_missing=default_currency_if_missing,
            request_timeout_seconds=request_timeout_seconds,
            rates_for_date=shared,
        )
