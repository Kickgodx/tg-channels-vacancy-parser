"""Пересчёт ``detected_salary_avg_rub`` для вакансий в БД."""

from __future__ import annotations

from dataclasses import dataclass

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db import fx_repository as fx_repo
from backend.db.models import VacancyRow
from backend.fx.apply import apply_salary_rub_for_vacancy
from backend.db.vacancy_mapper import row_to_vacancy, vacancy_to_row
from tg_jobs_parser.extraction.salary_period import (
    apply_hourly_currency_from_text,
    sanitize_salary_currency_period,
)
from tg_jobs_parser.config.schema import FxConfig


def _has_salary_condition():
    sal = func.coalesce(
        VacancyRow.detected_salary_avg,
        (VacancyRow.detected_salary_min + VacancyRow.detected_salary_max) / 2.0,
        VacancyRow.detected_salary_min,
        VacancyRow.detected_salary_max,
    )
    return sal.isnot(None)


@dataclass(frozen=True)
class SalaryRubBackfillStats:
    processed: int = 0
    updated: int = 0
    skipped_no_rate: int = 0


def backfill_vacancies_salary_rub(
    session: Session,
    config: FxConfig,
    *,
    db_rates_only: bool = False,
) -> SalaryRubBackfillStats:
    """
    Пересчитывает RUB-колонку для всех вакансий с указанной зарплатой.

    ``db_rates_only=True`` — только последний снимок из ``exchange_rates``, без запросов к ЦБ.
    """
    rates_cache: dict[date, dict[str, float]] = {}
    if config.enabled:
        if db_rates_only:
            rate_date, rates = fx_repo.get_latest_rates(session)
        else:
            rate_date, rates = fx_repo.ensure_latest_rates(session, config)
        if rate_date and rates:
            rates_cache = {rate_date: rates}

    stmt = select(VacancyRow).where(
        _has_salary_condition(),
        VacancyRow.is_duplicate.is_(False),
    )
    processed = 0
    updated = 0
    skipped = 0
    for row in session.scalars(stmt).yield_per(200):
        processed += 1
        v = row_to_vacancy(row)
        apply_hourly_currency_from_text(v)
        sanitize_salary_currency_period(v)
        apply_salary_rub_for_vacancy(
            v,
            session,
            config,
            force=True,
            rates_cache=rates_cache,
            db_rates_only=db_rates_only,
        )
        fresh = vacancy_to_row(v, parse_run_id=row.last_parse_run_id)
        row.detected_salary_avg_rub = fresh.detected_salary_avg_rub
        row.salary_currency = fresh.salary_currency
        if row.detected_salary_avg_rub is not None:
            updated += 1
        else:
            skipped += 1
    return SalaryRubBackfillStats(
        processed=processed,
        updated=updated,
        skipped_no_rate=skipped,
    )
