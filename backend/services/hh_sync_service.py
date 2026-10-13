"""Синхронизация вакансий HH: search → listings → dedup → detail (если нужно) → DB."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from opentelemetry import trace
from sqlalchemy.orm import Session

from backend.db import hh_repository as hh_repo
from backend.db.vacancy_mapper import row_to_vacancy
from backend.observability import (
    record_hh_api_error,
    record_hh_details,
    record_hh_sync_finished,
)
from backend.services.hh_sync_window import ResolvedHhSearchWindow
from backend.settings import Settings
from tg_jobs_parser.config.schema import AppConfig, HhConfig
from tg_jobs_parser.hh.client import HHApiError, HHClient, HHClientConfig
from tg_jobs_parser.hh.dedup import DedupedVacancy, dedupe_vacancies
from tg_jobs_parser.hh.mapper import (
    detail_to_vacancy,
    hh_source_post_key,
    short_to_vacancy,
    stable_vacancy_id,
)
from tg_jobs_parser.hh.models import VacancySearchParams, VacancyShort
from tg_jobs_parser.models.vacancy import Vacancy

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)
# In-process lock (SQLite/тесты + доп. защита в одном worker).
_SYNC_LOCK = threading.Lock()


class HhSyncConflict(RuntimeError):
    """Параллельный sync уже идёт."""


class HhSyncNotConfigured(RuntimeError):
    """HH выключен или нет токена/email."""


@dataclass
class HhSyncResult:
    searched: int = 0
    listings_upserted: int = 0
    unique: int = 0
    duplicates: int = 0
    details_fetched: int = 0
    details_skipped: int = 0
    vacancies_inserted: int = 0
    vacancies_updated: int = 0
    listings_purged: int = 0
    pages: int = 0
    flush_batches: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    window: str = "all"
    period: int | None = None
    date_from: str | None = None
    date_to: str | None = None


def _client_from(settings: Settings, hh: HhConfig) -> HHClient:
    token = (settings.hh_api_token or "").strip()
    email = (settings.hh_contact_email or "").strip()
    if not hh.enabled:
        raise HhSyncNotConfigured("HH sync disabled (hh.enabled=false)")
    if not token:
        raise HhSyncNotConfigured("HH_API_TOKEN is not set")
    if not email:
        raise HhSyncNotConfigured("HH_CONTACT_EMAIL is not set")
    return HHClient(
        HHClientConfig(
            api_token=token,
            contact_email=email,
            api_base=hh.api_base,
            host=hh.host,
            app_name=hh.app_name,
            app_version=hh.app_version,
            max_rps=hh.max_rps,
        )
    )


def ensure_hh_ready(settings: Settings, hh: HhConfig) -> None:
    """Проверить конфиг без открытия HTTP-клиента."""
    token = (settings.hh_api_token or "").strip()
    email = (settings.hh_contact_email or "").strip()
    if not hh.enabled:
        raise HhSyncNotConfigured("HH sync disabled (hh.enabled=false)")
    if not token:
        raise HhSyncNotConfigured("HH_API_TOKEN is not set")
    if not email:
        raise HhSyncNotConfigured("HH_CONTACT_EMAIL is not set")


def run_hh_sync(
    session: Session,
    *,
    config: AppConfig,
    settings: Settings,
    search_window: ResolvedHhSearchWindow | None = None,
) -> HhSyncResult:
    thread_locked = _SYNC_LOCK.acquire(blocking=False)
    if not thread_locked:
        raise HhSyncConflict("HH sync already in progress")
    db_lock = hh_repo.HhSyncDbLock(session)
    started = time.monotonic()
    result = HhSyncResult()
    outcome = "success"
    resolved = search_window or ResolvedHhSearchWindow(window="all")
    result.window = resolved.window
    result.period = resolved.period
    result.date_from = resolved.date_from
    result.date_to = resolved.date_to
    try:
        if not db_lock.try_acquire():
            raise HhSyncConflict("HH sync already in progress")

        with _TRACER.start_as_current_span("hh.sync") as span:
            hh = config.hh
            client = _client_from(settings, hh)
            try:
                span.set_attribute("hh.window", resolved.window)
                if resolved.period is not None:
                    span.set_attribute("hh.period", resolved.period)
                if resolved.date_from:
                    span.set_attribute("hh.date_from", resolved.date_from)
                if resolved.date_to:
                    span.set_attribute("hh.date_to", resolved.date_to)
                shorts, pages = _collect_shorts(client, hh, resolved)
                result.pages = pages
                result.searched = len(shorts)
                span.set_attribute("hh.searched", result.searched)
                span.set_attribute("hh.pages", pages)

                with _TRACER.start_as_current_span("hh.listings_upsert"):
                    result.listings_upserted = hh_repo.upsert_listings_from_shorts(session, shorts)
                    session.commit()

                by_id = {s.id: s for s in shorts}
                with _TRACER.start_as_current_span("hh.dedup") as dedup_span:
                    groups = _stabilize_groups(session, dedupe_vacancies(shorts), by_id)
                    result.unique = len(groups)
                    result.duplicates = sum(len(g.duplicates) for g in groups)
                    dedup_span.set_attribute("hh.unique", result.unique)
                    dedup_span.set_attribute("hh.duplicates", result.duplicates)
                shorts.clear()

                batch_size = hh.db_flush_batch_size
                buffer: list[Vacancy] = []

                def flush_buffer(*, force: bool = False) -> None:
                    if not buffer:
                        return
                    if not force and len(buffer) < batch_size:
                        return
                    with _TRACER.start_as_current_span("hh.upsert") as upsert_span:
                        stats = hh_repo.upsert_hh_vacancies(session, buffer, fx_config=config.fx)
                        result.vacancies_inserted += stats.inserted
                        result.vacancies_updated += stats.updated_existing
                        result.flush_batches += 1
                        upsert_span.set_attribute("hh.batch_size", len(buffer))
                        upsert_span.set_attribute("hh.inserted", stats.inserted)
                        upsert_span.set_attribute("hh.updated", stats.updated_existing)
                    session.commit()
                    _LOG.info(
                        "HH flush batch #%s: %s vacancies (+%s/~%s)",
                        result.flush_batches,
                        len(buffer),
                        stats.inserted,
                        stats.updated_existing,
                    )
                    buffer.clear()

                for group in groups:
                    _process_group(
                        client,
                        session,
                        group,
                        by_id,
                        result,
                        buffer,
                        on_buffered=lambda: flush_buffer(force=False),
                    )
                    by_id.pop(group.id, None)
                    for dup_id, _ in group.duplicates:
                        by_id.pop(dup_id, None)
                    flush_buffer()

                flush_buffer(force=True)
                groups.clear()
                by_id.clear()

                result.listings_purged = hh_repo.purge_expired_listings(session, ttl_days=hh.listing_ttl_days)
                session.commit()
                span.set_attribute("hh.details_fetched", result.details_fetched)
                span.set_attribute("hh.details_skipped", result.details_skipped)
                span.set_attribute("hh.flush_batches", result.flush_batches)
            finally:
                client.close()
    except HhSyncNotConfigured:
        outcome = "not_configured"
        raise
    except HHApiError as exc:
        outcome = "api_error"
        record_hh_api_error(status=str(exc.status_code or "unknown"))
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        try:
            db_lock.release()
        except Exception as exc:
            _LOG.warning("HH advisory lock release failed: %s", exc)
        if thread_locked:
            _SYNC_LOCK.release()
        result.duration_seconds = time.monotonic() - started
        record_hh_sync_finished(
            outcome=outcome,
            duration_seconds=result.duration_seconds,
            searched=result.searched,
        )
        record_hh_details(fetched=result.details_fetched, skipped=result.details_skipped)
        _LOG.info(
            "HH sync done in %.1fs: window=%s period=%s date_from=%s date_to=%s "
            "searched=%s unique=%s dup=%s detail_fetch=%s skip=%s vac +%s/~%s "
            "batches=%s errors=%s",
            result.duration_seconds,
            result.window,
            result.period,
            result.date_from,
            result.date_to,
            result.searched,
            result.unique,
            result.duplicates,
            result.details_fetched,
            result.details_skipped,
            result.vacancies_inserted,
            result.vacancies_updated,
            result.flush_batches,
            len(result.errors),
        )
    return result


def _collect_shorts(
    client: HHClient,
    hh: HhConfig,
    search_window: ResolvedHhSearchWindow | None = None,
) -> tuple[list[VacancyShort], int]:
    resolved = search_window or ResolvedHhSearchWindow(window="all")
    params = VacancySearchParams(
        text=hh.search.text,
        professional_role=list(hh.search.professional_role),
        per_page=hh.search.per_page,
        period=resolved.period,
        date_from=resolved.date_from,
        date_to=resolved.date_to,
    )
    shorts: list[VacancyShort] = []
    pages = 0
    with _TRACER.start_as_current_span("hh.search"):
        for page in client.iter_vacancy_pages(params):
            pages += 1
            shorts.extend(page.items)
    return shorts, pages


def _stabilize_groups(
    session: Session,
    groups: list[DedupedVacancy],
    by_id: dict[str, VacancyShort],
) -> list[DedupedVacancy]:
    """
    Канон = участник группы, у которого уже есть detail (если есть).

    Иначе порядок выдачи HH при повторном sync меняет канон, и бывшая
    detail-карточка попадает в duplicates и затирается short-stub'ом.
    """
    out: list[DedupedVacancy] = []
    for group in groups:
        members: list[tuple[str, str | None]] = [(group.id, group.area)]
        members.extend(group.duplicates)
        preferred_id: str | None = None
        for mid, _area in members:
            if hh_repo.listing_detail_fetched(session, mid):
                preferred_id = mid
                break
        if preferred_id is None or preferred_id == group.id:
            out.append(group)
            continue
        short = by_id.get(preferred_id)
        preferred_area = next((a for mid, a in members if mid == preferred_id), None)
        new_dups = [(mid, area) for mid, area in members if mid != preferred_id]
        out.append(
            DedupedVacancy(
                id=preferred_id,
                name=short.name if short else group.name,
                employer=(short.employer.name if short and short.employer else group.employer),
                area=preferred_area,
                url=short.alternate_url if short else group.url,
                duplicates=new_dups,
            )
        )
        _LOG.debug(
            "HH dedup: канон %s → %s (уже есть detail)",
            group.id,
            preferred_id,
        )
    return out


def _process_group(
    client: HHClient,
    session: Session,
    group: DedupedVacancy,
    by_id: dict[str, VacancyShort],
    result: HhSyncResult,
    buffer: list[Vacancy],
    *,
    on_buffered: Callable[[], None],
) -> None:
    canonical_id = group.id
    group_vacancy_id = stable_vacancy_id(canonical_id)
    short = by_id.get(canonical_id)
    if short is None:
        return

    if hh_repo.listing_detail_fetched(session, canonical_id):
        result.details_skipped += 1
        existing = hh_repo.get_vacancy_by_source_post_key(session, hh_source_post_key(canonical_id))
        if existing is not None:
            v = row_to_vacancy(existing)
            v.is_duplicate = False
            v.duplicate_group_id = None
            v.duplicate_reason = None
            buffer.append(v)
        else:
            _fetch_into_buffer(client, session, short, result, buffer, is_duplicate=False)
    else:
        _fetch_into_buffer(client, session, short, result, buffer, is_duplicate=False)
    on_buffered()

    for dup_id, _area in group.duplicates:
        dup_short = by_id.get(dup_id)
        if dup_short is None:
            continue
        _append_duplicate(
            client,
            session,
            dup_short,
            result,
            buffer,
            group_vacancy_id=group_vacancy_id,
        )
        on_buffered()


def _append_duplicate(
    client: HHClient,
    session: Session,
    short: VacancyShort,
    result: HhSyncResult,
    buffer: list[Vacancy],
    *,
    group_vacancy_id: str,
) -> None:
    """Дубль города: не затирать уже сохранённый detail short-stub'ом."""
    key = hh_source_post_key(short.id)
    existing = hh_repo.get_vacancy_by_source_post_key(session, key)
    if existing is not None:
        v = row_to_vacancy(existing)
        v.is_duplicate = True
        v.duplicate_group_id = group_vacancy_id
        v.duplicate_reason = "hh_multicity"
        buffer.append(v)
        return
    if hh_repo.listing_detail_fetched(session, short.id):
        # detail был, строки нет — перекачаем, не деградируем до short
        _fetch_into_buffer(
            client,
            session,
            short,
            result,
            buffer,
            is_duplicate=True,
            duplicate_group_id=group_vacancy_id,
            duplicate_reason="hh_multicity",
        )
        return
    buffer.append(
        short_to_vacancy(
            short,
            is_duplicate=True,
            duplicate_group_id=group_vacancy_id,
            duplicate_reason="hh_multicity",
        )
    )


def _fetch_into_buffer(
    client: HHClient,
    session: Session,
    short: VacancyShort,
    result: HhSyncResult,
    buffer: list[Vacancy],
    *,
    is_duplicate: bool,
    duplicate_group_id: str | None = None,
    duplicate_reason: str | None = None,
) -> None:
    try:
        with _TRACER.start_as_current_span("hh.detail") as span:
            span.set_attribute("hh.vacancy_id", short.id)
            detail = client.get_vacancy(short.id)
        vac = detail_to_vacancy(
            detail,
            is_duplicate=is_duplicate,
            duplicate_group_id=duplicate_group_id,
            duplicate_reason=duplicate_reason,
        )
        hh_repo.mark_detail_fetched(session, short.id)
        result.details_fetched += 1
        buffer.append(vac)
    except HHApiError as exc:
        record_hh_api_error(status=str(exc.status_code or "unknown"))
        msg = f"detail {short.id}: HTTP {exc.status_code}"
        result.errors.append(msg)
        _LOG.warning("HH %s", msg)
        buffer.append(
            short_to_vacancy(
                short,
                is_duplicate=is_duplicate,
                duplicate_group_id=duplicate_group_id,
                duplicate_reason=duplicate_reason,
            )
        )
