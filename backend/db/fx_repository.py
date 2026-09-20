"""Курсы валют в PostgreSQL / SQLite."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.models import ExchangeRateRow
from backend.observability import record_fx_fetch
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.fx.cbr import fetch_cbr_rates
from tg_jobs_parser.fx.convert import currency_for_rate_lookup, is_ruble_currency

_LOG = logging.getLogger(__name__)


def get_rates(session: Session, rate_date: date) -> dict[str, float]:
    rows = session.scalars(
        select(ExchangeRateRow).where(ExchangeRateRow.rate_date == rate_date)
    ).all()
    if not rows:
        return {}
    return {r.currency: float(r.rate_to_rub) for r in rows}


def get_rate_to_rub(
    session: Session,
    currency: str,
    rate_date: date,
    *,
    config: FxConfig | None = None,
) -> float | None:
    """
    Курс валюты к рублю на дату; если на день нет — последний известный курс на эту дату или раньше.
    """
    cur = str(currency).upper().strip()
    if is_ruble_currency(cur):
        return 1.0
    lookup = currency_for_rate_lookup(cur) or cur
    rates = get_rates(session, rate_date)
    if lookup in rates:
        return rates[lookup]
    row = session.scalar(
        select(ExchangeRateRow.rate_to_rub)
        .where(
            ExchangeRateRow.currency == lookup,
            ExchangeRateRow.rate_date <= rate_date,
        )
        .order_by(ExchangeRateRow.rate_date.desc())
        .limit(1)
    )
    if row is not None:
        return float(row)
    if config is None or not config.enabled:
        return None
    ensure_rates(session, rate_date, config)
    rates = get_rates(session, rate_date)
    if lookup in rates:
        return rates[lookup]
    return None


def upsert_rates(
    session: Session,
    rate_date: date,
    rates: dict[str, float],
    *,
    source: str = "cbr",
) -> int:
    if not rates:
        return 0
    now = datetime.now(timezone.utc)
    count = 0
    for currency, rate in rates.items():
        if not currency or rate is None:
            continue
        cur = str(currency).upper()
        data = {
            "rate_date": rate_date,
            "currency": cur,
            "rate_to_rub": float(rate),
            "source": source,
            "fetched_at": now,
        }
        dialect = session.get_bind().dialect.name
        ins = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = ins(ExchangeRateRow).values(**data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["rate_date", "currency"],
            set_={
                "rate_to_rub": float(rate),
                "source": source,
                "fetched_at": now,
            },
        )
        session.execute(stmt)
        count += 1
    return count


def ensure_rates(
    session: Session,
    rate_date: date,
    config: FxConfig,
) -> dict[str, float]:
    """Курсы на дату из БД или загрузка с ЦБ."""
    rates = get_rates(session, rate_date)
    if rates:
        return rates
    if not config.enabled:
        return {"RUB": 1.0}
    try:
        actual_date, fetched = fetch_cbr_rates(rate_date, timeout=config.request_timeout_seconds)
        upsert_rates(session, actual_date, fetched, source=config.provider)
        session.flush()
        if actual_date != rate_date:
            upsert_rates(session, rate_date, fetched, source=config.provider)
            session.flush()
        record_fx_fetch("fetched")
        return fetched
    except Exception as e:
        record_fx_fetch("failed")
        _LOG.warning("ЦБ: курс на %s не загружен: %s", rate_date, e)
        return {}


def ensure_rates_for_dates(session: Session, dates: set[date], config: FxConfig) -> None:
    """Догружает в БД только отсутствующие даты (по одному HTTP-запросу на дату)."""
    missing = [d for d in dates if not get_rates(session, d)]
    if not missing:
        return
    _LOG.info("ЦБ: загрузка курсов для %s дат (из %s уникальных)…", len(missing), len(dates))
    for d in sorted(missing):
        ensure_rates(session, d, config)


def get_latest_rate_date(session: Session) -> date | None:
    return session.scalar(select(func.max(ExchangeRateRow.rate_date)))


def get_latest_rates(session: Session) -> tuple[date | None, dict[str, float]]:
    latest = get_latest_rate_date(session)
    if latest is None:
        return None, {}
    return latest, get_rates(session, latest)


def ensure_latest_rates(session: Session, config: FxConfig) -> tuple[date | None, dict[str, float]]:
    """
    Курсы для лайв-отображения: догрузить сегодня в БД при необходимости, вернуть последнюю дату в кэше.
    """
    if not config.enabled:
        return None, {}
    today = datetime.now(timezone.utc).date()
    ensure_rates(session, today, config)
    return get_latest_rates(session)


def refresh_rates_from_api(session: Session, config: FxConfig) -> tuple[date, dict[str, float], int]:
    """Принудительно загружает курсы ЦБ на сегодня и сохраняет в БД."""
    today = datetime.now(timezone.utc).date()
    if not config.enabled:
        upsert_rates(session, today, {"RUB": 1.0}, source=config.provider)
        return today, {"RUB": 1.0}, 1
    try:
        actual_date, fetched = fetch_cbr_rates(today, timeout=config.request_timeout_seconds)
    except Exception:
        record_fx_fetch("failed")
        raise
    record_fx_fetch("fetched")
    n = upsert_rates(session, actual_date, fetched, source=config.provider)
    session.flush()
    return actual_date, fetched, n


def sync_recent_days(session: Session, config: FxConfig, *, days: int | None = None) -> int:
    """Загружает курсы за последние N календарных дней (включая сегодня)."""
    if not config.enabled:
        return 0
    n = days if days is not None else config.sync_days_on_startup
    if n <= 0:
        return 0
    today = datetime.now(timezone.utc).date()
    total = 0
    for i in range(n):
        d = today - timedelta(days=i)
        before = len(get_rates(session, d))
        ensure_rates(session, d, config)
        after = len(get_rates(session, d))
        if after > before:
            total += after - before
        # Коммит после каждого дня: иначе write-lock держится на все HTTP-запросы
        # к ЦБ и на SQLite параллельные писатели падают с "database is locked".
        session.commit()
    return total
