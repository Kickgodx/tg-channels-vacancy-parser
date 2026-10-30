"""Курсы валют: обновление из ЦБ и пересчёт зарплат в рублях."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, require_admin, verify_api_key
from backend.db import fx_repository as fx_repo
from backend.fx.backfill import backfill_vacancies_salary_rub
from backend.settings import get_settings
from jobdesk.config.loader import load_config

# Курсы читает любой залогиненный пользователь; обновление и пересчёт — только admin.
router = APIRouter(
    prefix="/fx",
    tags=["fx"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)


class FxRateItem(BaseModel):
    currency: str
    rate_to_rub: float


class FxRatesResponse(BaseModel):
    rate_date: date | None = None
    source: str = "cbr"
    rates: list[FxRateItem] = Field(default_factory=list)


class FxRefreshResponse(BaseModel):
    rate_date: date
    rates_count: int
    history_days_synced: int
    vacancies_processed: int
    vacancies_updated_rub: int
    vacancies_skipped_no_rate: int


class FxRecalculateRubResponse(BaseModel):
    rate_date: date | None = None
    vacancies_processed: int
    vacancies_updated_rub: int
    vacancies_skipped_no_rate: int


@router.get("/rates", response_model=FxRatesResponse)
def list_fx_rates(db: Session = Depends(get_db)) -> FxRatesResponse:
    """Актуальные курсы из БД (последняя дата в ``exchange_rates``)."""
    cfg = load_config(get_settings().config_path)
    rate_date, rates = fx_repo.get_latest_rates(db)
    items = [FxRateItem(currency=c, rate_to_rub=r) for c, r in sorted(rates.items(), key=lambda x: x[0])]
    return FxRatesResponse(
        rate_date=rate_date,
        source=cfg.fx.provider,
        rates=items,
    )


@router.post("/refresh", response_model=FxRefreshResponse, dependencies=[Depends(require_admin)])
def refresh_fx_rates(db: Session = Depends(get_db)) -> FxRefreshResponse:
    """
    Загрузить курсы из API ЦБ, сохранить в ``exchange_rates``,
    пересчитать ``detected_salary_avg_rub`` у всех вакансий с зарплатой.
    """
    cfg = load_config(get_settings().config_path)
    rate_date, _rates, rates_count = fx_repo.refresh_rates_from_api(db, cfg.fx)
    history_synced = fx_repo.sync_recent_days(db, cfg.fx)
    backfill = backfill_vacancies_salary_rub(db, cfg.fx)
    return FxRefreshResponse(
        rate_date=rate_date,
        rates_count=rates_count,
        history_days_synced=history_synced,
        vacancies_processed=backfill.processed,
        vacancies_updated_rub=backfill.updated,
        vacancies_skipped_no_rate=backfill.skipped_no_rate,
    )


@router.post(
    "/recalculate-rub",
    response_model=FxRecalculateRubResponse,
    dependencies=[Depends(require_admin)],
)
def recalculate_salary_rub(db: Session = Depends(get_db)) -> FxRecalculateRubResponse:
    """
    Пересчитать ``detected_salary_avg_rub`` по последнему курсу в БД, без запроса к ЦБ.
    """
    cfg = load_config(get_settings().config_path)
    if not cfg.fx.enabled:
        raise HTTPException(status_code=400, detail="Конвертация валют отключена в config (fx.enabled).")
    rate_date, rates = fx_repo.get_latest_rates(db)
    if rate_date is None or not rates:
        raise HTTPException(
            status_code=400,
            detail="В БД нет курсов. Сначала нажмите «Обновить курсы ЦБ».",
        )
    backfill = backfill_vacancies_salary_rub(db, cfg.fx, db_rates_only=True)
    return FxRecalculateRubResponse(
        rate_date=rate_date,
        vacancies_processed=backfill.processed,
        vacancies_updated_rub=backfill.updated,
        vacancies_skipped_no_rate=backfill.skipped_no_rate,
    )
