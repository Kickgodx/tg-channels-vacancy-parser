"""Применение курсов к вакансиям при записи в БД (курс на сегодня / последний в кэше)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.db import fx_repository as fx_repo
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.fx.convert import currency_for_rate_lookup, normalize_currency
from tg_jobs_parser.fx.vacancy_rub import effective_salary_amount_for_vacancy
from tg_jobs_parser.models.vacancy import Vacancy


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
) -> None:
    if not force and v.detected_salary_avg_rub is not None:
        return
    amount = effective_salary_amount_for_vacancy(v)
    if amount is None:
        v.detected_salary_avg_rub = None
        return
    cur = normalize_currency(
        v.salary_currency,
        default_if_missing=config.default_currency_if_missing,
    )
    if cur == "RUB":
        v.detected_salary_avg_rub = float(amount)
        return
    if not config.enabled:
        v.detected_salary_avg_rub = None
        return
    _, rates = _rates_from_cache(rates_cache)
    if not rates:
        _, rates = fx_repo.ensure_latest_rates(session, config)
    lookup = currency_for_rate_lookup(cur) if cur else None
    rate = rates.get(lookup) if lookup and rates else None
    v.detected_salary_avg_rub = float(amount) * rate if rate is not None else None


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
