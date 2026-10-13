"""Репозиторий кэша HH listings и upsert вакансий по source_post_key."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from backend.db.models import HhListingRow, VacancyRow
from backend.db.vacancy_mapper import vacancy_to_row
from backend.db.vacancy_repository import VacancyUpsertStats, invalidate_vacancy_count_cache
from backend.fx.apply import (
    apply_salary_rub_for_vacancy,
    preload_fx_rates_for_vacancies,
)
from backend.observability import record_hh_listings_purged, record_vacancy_upsert
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.extraction.grade_extractor import UNKNOWN_GRADE
from tg_jobs_parser.hh.dedup import vacancy_key_hex
from tg_jobs_parser.hh.grades import grade_from_experience_name
from tg_jobs_parser.hh.mapper import HH_CHANNEL, hh_source_post_key, parse_hh_datetime
from tg_jobs_parser.hh.models import VacancyShort
from tg_jobs_parser.models.vacancy import Vacancy

_LOG = logging.getLogger(__name__)

# Произвольный ключ pg_advisory_lock для HH sync (межпроцессный 409).
HH_SYNC_ADVISORY_LOCK_KEY = 874_291_001


@dataclass(frozen=True)
class HhListingCounts:
    total: int = 0
    with_detail: int = 0
    without_detail: int = 0


def _fx_config(fx_config: FxConfig | None) -> FxConfig:
    if fx_config is not None:
        return fx_config
    try:
        return load_config(get_settings().config_path).fx
    except Exception:
        return FxConfig(enabled=False)


def _listing_ttl_days() -> int:
    try:
        return load_config(get_settings().config_path).hh.listing_ttl_days
    except Exception:
        return 40


def _clip(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _is_later(new: datetime | None, old: datetime | None) -> bool:
    """``new`` строго позже ``old``; naive-значения (SQLite) считаются UTC."""
    if new is None or old is None:
        return False
    new_utc = new if new.tzinfo else new.replace(tzinfo=UTC)
    old_utc = old if old.tzinfo else old.replace(tzinfo=UTC)
    return new_utc > old_utc


def get_listing(session: Session, hh_id: str) -> HhListingRow | None:
    return session.get(HhListingRow, hh_id)


def listing_detail_fetched(session: Session, hh_id: str) -> bool:
    row = session.get(HhListingRow, hh_id)
    return row is not None and row.detail_fetched_at is not None


def mark_detail_fetched(session: Session, hh_id: str, *, when: datetime | None = None) -> None:
    row = session.get(HhListingRow, hh_id)
    if row is None:
        return
    row.detail_fetched_at = when or datetime.now(UTC)
    row.updated_at = datetime.now(UTC)


class HhSyncDbLock:
    """Межпроцессный lock HH sync: Postgres advisory на выделенном соединении.

    Advisory-lock принадлежит соединению, а sync коммитит по ходу работы: после
    commit сессия возвращает соединение в пул и дальше может получить другое.
    Lock на соединении сессии поэтому «протекал» — unlock уходил не туда, и
    следующий sync получал 409 до рестарта. Здесь lock живёт на своём соединении
    (AUTOCOMMIT — без idle-in-transaction на всё время sync) от ``try_acquire``
    до ``release``. На SQLite — no-op (там хватает threading-lock).
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._conn: Connection | None = None

    def try_acquire(self) -> bool:
        bind = self._session.get_bind()
        if bind.dialect.name != "postgresql":
            return True
        conn = bind.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": HH_SYNC_ADVISORY_LOCK_KEY}).scalar())
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return False
        self._conn = conn
        return True

    def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": HH_SYNC_ADVISORY_LOCK_KEY})
        except Exception as exc:
            # Вернуть в пул соединение с неснятым lock нельзя: закрываем его совсем —
            # Postgres освобождает advisory-lock вместе с сессией.
            _LOG.warning("HH advisory unlock failed, соединение закрыто: %s", exc)
            conn.invalidate()
        finally:
            conn.close()


def upsert_listings_from_shorts(
    session: Session,
    shorts: list[VacancyShort],
    *,
    now: datetime | None = None,
) -> int:
    """
    Upsert короткой выдачи.

    Если ``content_hash`` изменился — сбрасываем ``detail_fetched_at``,
    чтобы следующий sync заново скачал detail.
    """
    if not shorts:
        return 0
    now = now or datetime.now(UTC)
    count = 0
    reset_detail = 0
    for short in shorts:
        salary = short.salary_range or short.salary
        salary_json = salary.model_dump(mode="json", by_alias=True) if salary is not None else None
        snippet_json = short.snippet.model_dump(mode="json") if short.snippet is not None else None
        raw = short.model_dump(mode="json", by_alias=True)
        new_hash = vacancy_key_hex(short)
        values = {
            "hh_id": short.id,
            "name": (short.name or "")[:512],
            "employer": _clip(short.employer.name if short.employer else None, 256),
            "area": _clip(short.area.name if short.area else None, 256),
            "alternate_url": short.alternate_url,
            "published_at": parse_hh_datetime(short.published_at),
            "content_hash": new_hash,
            "salary_json": salary_json,
            "snippet_json": snippet_json,
            "raw_json": raw,
            "last_seen_at": now,
            "updated_at": now,
            "created_at": now,
        }
        existing = session.get(HhListingRow, short.id)
        if existing is None:
            session.add(HhListingRow(**values, detail_fetched_at=None))
        else:
            hash_changed = existing.content_hash != new_hash
            existing.name = values["name"]
            existing.employer = values["employer"]
            existing.area = values["area"]
            existing.alternate_url = values["alternate_url"]
            existing.published_at = values["published_at"]
            existing.content_hash = values["content_hash"]
            existing.salary_json = values["salary_json"]
            existing.snippet_json = values["snippet_json"]
            existing.raw_json = values["raw_json"]
            existing.last_seen_at = now
            existing.updated_at = now
            if hash_changed and existing.detail_fetched_at is not None:
                existing.detail_fetched_at = None
                reset_detail += 1
        count += 1
    session.flush()
    if reset_detail:
        _LOG.info(
            "HH listings: content_hash изменился у %s — detail_fetched_at сброшен",
            reset_detail,
        )
    return count


def purge_expired_listings(
    session: Session,
    *,
    now: datetime | None = None,
    ttl_days: int | None = None,
) -> int:
    ttl = ttl_days if ttl_days is not None else _listing_ttl_days()
    if ttl <= 0:
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=ttl)
    result = session.execute(delete(HhListingRow).where(HhListingRow.last_seen_at < cutoff))
    deleted = int(result.rowcount or 0)
    if deleted:
        _LOG.info("HH listings purged: %s (ttl=%s d)", deleted, ttl)
        record_hh_listings_purged(deleted)
    return deleted


def backfill_hh_grades_from_experience(session: Session) -> int:
    """Идемпотентно проставить detected_grade HH-вакансиям по required_experience."""
    rows = session.scalars(
        select(VacancyRow).where(
            VacancyRow.source_channel == HH_CHANNEL,
            VacancyRow.detected_grade == UNKNOWN_GRADE,
            VacancyRow.required_experience.is_not(None),
        )
    ).all()
    updated = 0
    for row in rows:
        grade = grade_from_experience_name(row.required_experience)
        if grade == UNKNOWN_GRADE:
            continue
        row.detected_grade = grade
        updated += 1
    if updated:
        session.flush()
        invalidate_vacancy_count_cache()
        _LOG.info("HH grades backfill: обновлено %s вакансий", updated)
    return updated


def listing_counts(session: Session) -> HhListingCounts:
    total = int(session.scalar(select(func.count()).select_from(HhListingRow)) or 0)
    with_detail = int(
        session.scalar(
            select(func.count()).select_from(HhListingRow).where(HhListingRow.detail_fetched_at.is_not(None))
        )
        or 0
    )
    return HhListingCounts(
        total=total,
        with_detail=with_detail,
        without_detail=total - with_detail,
    )


def get_vacancy_by_source_post_key(session: Session, source_post_key: str) -> VacancyRow | None:
    return session.scalar(select(VacancyRow).where(VacancyRow.source_post_key == source_post_key).limit(1))


def upsert_hh_vacancies(
    session: Session,
    vacancies: list[Vacancy],
    *,
    fx_config: FxConfig | None = None,
) -> VacancyUpsertStats:
    """
    Insert/update HH-вакансий по ``source_post_key``.

    При совпадении перезаписываются mapped-поля (в отличие от TG content-hash upsert).
    """
    if not vacancies:
        return VacancyUpsertStats()
    now = datetime.now(UTC)
    fx = _fx_config(fx_config)
    rates_cache = preload_fx_rates_for_vacancies(session, vacancies, fx)
    inserted = 0
    updated_existing = 0
    for v in vacancies:
        key = v.source_post_key or hh_source_post_key(v.id)
        existing = get_vacancy_by_source_post_key(session, key)
        is_existing = existing is not None
        apply_salary_rub_for_vacancy(v, session, fx, rates_cache=rates_cache, force=is_existing)
        row = vacancy_to_row(v, parse_run_id=None)
        data = {c.name: getattr(row, c.name) for c in VacancyRow.__table__.columns}
        data.pop("updated_at", None)
        if not data.get("parsed_at"):
            data["parsed_at"] = now
        if existing is not None:
            data["id"] = existing.id
            data["parsed_at"] = existing.parsed_at or data["parsed_at"]
            data["last_parse_run_id"] = existing.last_parse_run_id
            # HH поднял вакансию заново (published_at новее) — возвращаем из архива.
            republished = _is_later(data["post_date"], existing.post_date)
            data["archived_at"] = None if republished else existing.archived_at

        dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
        if dialect == "postgresql":
            stmt = pg_insert(VacancyRow).values(**data)
            update_cols = {
                k: data[k]
                for k in data
                if k
                not in (
                    "id",
                    "parsed_at",
                    "last_parse_run_id",
                    "created_at",
                )
            }
            update_cols["updated_at"] = now
            stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
            session.execute(stmt)
        else:
            if existing is None:
                session.add(VacancyRow(**data))
            else:
                for k, val in data.items():
                    if k in ("id", "parsed_at", "last_parse_run_id"):
                        continue
                    setattr(existing, k, val)
                existing.updated_at = now
        if is_existing:
            updated_existing += 1
        else:
            inserted += 1
    session.flush()
    record_vacancy_upsert(inserted=inserted, updated_existing=updated_existing)
    if inserted or updated_existing:
        invalidate_vacancy_count_cache()
    return VacancyUpsertStats(inserted=inserted, updated_existing=updated_existing)
