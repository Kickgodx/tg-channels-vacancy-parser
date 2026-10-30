"""Применение курсов к вакансиям при записи в БД (курс на сегодня / последний в кэше)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.db import fx_repository as fx_repo
from jobdesk.config.schema import FxConfig
from jobdesk.fx.convert import (
    currency_for_rate_lookup,
    is_ruble_currency,
    monthly_salary_amount,
    normalize_currency,
)
from jobdesk.fx.vacancy_rub import effective_salary_amount_for_vacancy
from jobdesk.models.vacancy import Vacancy


def _rates_from_cache(
    rates_cache: dict[date, dict[str, float]] | None,
) -> tuple[date | None, dict[str, float]]:
    if not rates_cache:
        return None, {}
    d = max(rates_cache)
    return d, rates_cache[d]


def apply_salary_rub_for_vacancy(
    v: Vacancy,
    session: Session,
    config: FxConfig,
    *,
    rates_cache: dict[date, dict[str, float]] | None = None,
    force: bool = False,
    db_rates_only: bool = False,
) -> None:
    if not force and v.detected_salary_avg_rub is not None:
        return
    amount = effective_salary_amount_for_vacancy(v)
    if amount is None:
        v.detected_salary_avg_rub = None
        return
    monthly = monthly_salary_amount(amount, v.salary_currency)
    if monthly is None:
        v.detected_salary_avg_rub = None
        return
    # Зарплата без валюты считается в fx.default_currency_if_missing (как в метриках прогона);
    # сохранённая salary_currency не меняется. Пустая настройка — валюту не подставлять.
    currency = normalize_currency(v.salary_currency, default_if_missing=config.default_currency_if_missing)
    if is_ruble_currency(currency):
        v.detected_salary_avg_rub = monthly
        return
    if not config.enabled:
        v.detected_salary_avg_rub = None
        return
    _, rates = _rates_from_cache(rates_cache)
    # Переданный кэш, даже пустой, — попытка уже была (одна на пачку): при пустой базе и
    # недоступном ЦБ повтор на каждой вакансии стоил бы таймаута ЦБ на каждую.
    if rates_cache is None:
        if db_rates_only:
            _, rates = fx_repo.get_latest_rates(session)
        else:
            _, rates = fx_repo.ensure_latest_rates(session, config)
    lookup = currency_for_rate_lookup(currency)
    rate = rates.get(lookup) if lookup and rates else None
    v.detected_salary_avg_rub = monthly * rate if rate is not None else None


def preload_fx_rates_for_vacancies(
    session: Session,
    vacancies: list[Vacancy],
    config: FxConfig,
) -> dict[date, dict[str, float]]:
    """Один запрос к ЦБ/БД: последний курс (на сегодня), для всех вакансий батча."""
    if not config.enabled:
        return {}
    if not any(effective_salary_amount_for_vacancy(v) is not None for v in vacancies):
        return {}
    rate_date, rates = fx_repo.ensure_latest_rates(session, config)
    if not rate_date or not rates:
        return {}
    return {rate_date: rates}
