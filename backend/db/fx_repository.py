"""Курсы валют в PostgreSQL / SQLite."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.models import ExchangeRateRow
from backend.observability import record_fx_fetch
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.fx.cbr import fetch_cbr_rates

_LOG = logging.getLogger(__name__)


def get_rates(session: Session, rate_date: date) -> dict[str, float]:
    rows = session.scalars(select(ExchangeRateRow).where(ExchangeRateRow.rate_date == rate_date)).all()
    if not rows:
        return {}
    return {r.currency: float(r.rate_to_rub) for r in rows}


def upsert_rates(
    session: Session,
    rate_date: date,
    rates: dict[str, float],
    *,
    source: str = "cbr",
) -> int:
    if not rates:
        return 0
    now = datetime.now(UTC)
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


def get_latest_rate_date(session: Session) -> date | None:
    return session.scalar(select(func.max(ExchangeRateRow.rate_date)))


def get_latest_rates(session: Session) -> tuple[date | None, dict[str, float]]:
    latest = get_latest_rate_date(session)
    if latest is None:
        return None, {}
    return latest, get_rates(session, latest)


def ensure_latest_rates(
    session: Session,
    config: FxConfig,
    *,
    prefer_db: bool = True,
) -> tuple[date | None, dict[str, float]]:
    """Курсы для upsert / метрик прогона.

    По умолчанию ``prefer_db=True``: взять последний снимок из ``exchange_rates``
    без HTTP. В ЦБ идём только если в БД ещё нет ни одной даты (холодный старт).
    ``prefer_db=False`` — как раньше: попытаться догрузить «сегодня», затем latest.
    """
    if not config.enabled:
        return None, {}
    if prefer_db:
        latest_date, rates = get_latest_rates(session)
        if latest_date is not None and rates:
            return latest_date, rates
    today = datetime.now(UTC).date()
    ensure_rates(session, today, config)
    return get_latest_rates(session)


def purge_old_rates(session: Session, *, older_than: date, limit: int = 1000) -> int:
    """Удалить до ``limit`` курсов с датой раньше ``older_than``.

    Читается только последняя дата (``get_latest_rates``) — история нужна лишь для
    стартовой догрузки. Последняя дата остаётся всегда, даже если она старше
    ``older_than`` (ЦБ долго недоступен): без неё зарплаты в ₽ не посчитать.
    """
    latest = get_latest_rate_date(session)
    if latest is None:
        return 0
    cutoff = min(older_than, latest)
    old = (
        select(ExchangeRateRow.rate_date, ExchangeRateRow.currency)
        .where(ExchangeRateRow.rate_date < cutoff)
        .limit(limit)
    )
    result = session.execute(
        delete(ExchangeRateRow).where(tuple_(ExchangeRateRow.rate_date, ExchangeRateRow.currency).in_(old))
    )
    return int(result.rowcount or 0)


def refresh_rates_from_api(session: Session, config: FxConfig) -> tuple[date, dict[str, float], int]:
    """Принудительно загружает курсы ЦБ на сегодня и сохраняет в БД."""
    today = datetime.now(UTC).date()
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
    today = datetime.now(UTC).date()
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
