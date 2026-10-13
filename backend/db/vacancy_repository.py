"""Репозиторий вакансий (shared corpus + upsert / list / TTL)."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import and_, case, func, literal_column, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer

from backend.db.models import (
    VacancyMatchRow,
    VacancyRow,
)
from backend.db.vacancy_filters import apply_vacancy_filters, vacancy_count_stmt
from backend.db.vacancy_mapper import (
    row_to_list_item,
    row_to_vacancy,
    vacancy_preview_expr,
    vacancy_to_row,
)
from backend.fx.apply import (
    apply_salary_rub_for_vacancy,
    preload_fx_rates_for_vacancies,
)
from backend.observability import record_vacancies_archived, record_vacancy_upsert
from backend.schemas import VacancyListItem, VacancyQuery, VacancySortField
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.vacancy import Vacancy


@dataclass(frozen=True)
class VacancyUpsertStats:
    inserted: int = 0
    updated_existing: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated_existing


_LOG = logging.getLogger(__name__)

VACANCY_TTL_DAYS = 40  # fallback, если config.yaml не загружается

# Короткий кэш COUNT(*) списка: при нагрузочном чтении без фильтров режет seq/index scan пополам.
_COUNT_CACHE_TTL_S = 1.0
_count_cache_lock = Lock()
_count_cache: dict[str, tuple[float, int]] = {}


def _vacancy_count_cache_key(query: VacancyQuery, dialect: str) -> str:
    payload = query.model_dump(exclude={"page", "page_size", "sort_by", "sort_order"})
    return f"{dialect}:{sorted(payload.items())!r}"


def _get_cached_vacancy_count(key: str) -> int | None:
    with _count_cache_lock:
        hit = _count_cache.get(key)
        if hit is None:
            return None
        ts, total = hit
        if time.monotonic() - ts > _COUNT_CACHE_TTL_S:
            _count_cache.pop(key, None)
            return None
        return total


def _set_cached_vacancy_count(key: str, total: int) -> None:
    with _count_cache_lock:
        _count_cache[key] = (time.monotonic(), total)


def invalidate_vacancy_count_cache() -> None:
    with _count_cache_lock:
        _count_cache.clear()


def counted_vacancies(session: Session, query: VacancyQuery, *, dialect: str) -> int:
    key = _vacancy_count_cache_key(query, dialect)
    cached = _get_cached_vacancy_count(key)
    if cached is not None:
        return cached
    total = int(session.scalar(vacancy_count_stmt(query, dialect=dialect)) or 0)
    _set_cached_vacancy_count(key, total)
    return total


def _vacancy_ttl_days() -> int:
    try:
        return load_config(get_settings().config_path).retention.vacancy_ttl_days
    except Exception:
        return VACANCY_TTL_DAYS


_SORT_COLUMNS: dict[VacancySortField, object] = {
    "post_date": VacancyRow.post_date,
    "detected_salary_avg": VacancyRow.detected_salary_avg,
    "detected_salary_avg_rub": VacancyRow.detected_salary_avg_rub,
    "work_format": VacancyRow.work_format,
    "detected_grade": VacancyRow.detected_grade,
    "salary_currency": VacancyRow.salary_currency,
    "vacancy_score": VacancyRow.vacancy_score,
}


def _vacancy_order_by(query: VacancyQuery, *, dialect: str = "postgresql") -> list:
    if query.sort_by == "match_score" and query.resume_id is not None:
        col = VacancyMatchRow.overall_score
    else:
        col = _SORT_COLUMNS.get(query.sort_by, VacancyRow.post_date)
    order = col.asc().nulls_last() if query.sort_order == "asc" else col.desc().nulls_last()
    if query.q and dialect == "postgresql":
        rank = func.ts_rank(
            func.to_tsvector(literal_column("'russian'"), VacancyRow.cleaned_text),
            func.websearch_to_tsquery(literal_column("'russian'"), query.q),
        )
        return [rank.desc(), order]
    return [order]


def _fx_config(fx_config: FxConfig | None) -> FxConfig:
    if fx_config is not None:
        return fx_config
    return load_config(get_settings().config_path).fx


def upsert_vacancies(
    session: Session,
    vacancies: list[Vacancy],
    *,
    parse_run_id: uuid.UUID | None,
    fx_config: FxConfig | None = None,
) -> VacancyUpsertStats:
    """
    Вставка уникальных вакансий по ``id`` (content hash).

    При совпадении текста обновляются ``last_parse_run_id``, ``updated_at``, RUB-зарплата
    и AI-метаданные; извлечённые поля не трогаются. Если пост новее сохранённого
    (канал повторно опубликовал тот же текст), ``post_date`` сдвигается вперёд, а
    вакансия возвращается из архива — иначе TTL по первой публикации прятал бы её навсегда.
    ``parse_run_id=None`` — ручной импорт без прогона: существующая привязка
    к прогону при update не затирается.
    """
    if not vacancies:
        return VacancyUpsertStats()
    started = time.monotonic()
    now = datetime.now(UTC)
    ids = [v.id for v in vacancies]
    existing_ids = set(session.scalars(select(VacancyRow.id).where(VacancyRow.id.in_(ids))).all())
    fx = _fx_config(fx_config)
    _LOG.info("Сохранение %s вакансий в БД (уже есть %s)…", len(vacancies), len(existing_ids))
    rates_cache = preload_fx_rates_for_vacancies(session, vacancies, fx)
    if rates_cache:
        _LOG.info("Курсы для сохранения: %s дат в кэше", len(rates_cache))
    inserted = 0
    updated_existing = 0
    for v in vacancies:
        is_existing = v.id in existing_ids
        apply_salary_rub_for_vacancy(v, session, fx, rates_cache=rates_cache, force=is_existing)
        row = vacancy_to_row(v, parse_run_id=parse_run_id)
        data = {c.name: getattr(row, c.name) for c in VacancyRow.__table__.columns}
        data.pop("updated_at", None)
        if not data.get("parsed_at"):
            data["parsed_at"] = now
        stmt = insert(VacancyRow).values(**data)
        if is_existing:
            # CASE вместо GREATEST: в SQLite (тесты) его нет, а max() там не игнорирует NULL.
            reposted_later = stmt.excluded.post_date > VacancyRow.post_date
            conflict_set = {
                "updated_at": now,
                "detected_salary_avg_rub": data["detected_salary_avg_rub"],
                "ai_notes": data["ai_notes"],
                "ai_confidence": data["ai_confidence"],
                "extraction_source": data["extraction_source"],
                "post_date": case(
                    (VacancyRow.post_date.is_(None), stmt.excluded.post_date),
                    (reposted_later, stmt.excluded.post_date),
                    else_=VacancyRow.post_date,
                ),
                "archived_at": case((reposted_later, null()), else_=VacancyRow.archived_at),
            }
            if parse_run_id is not None:
                conflict_set["last_parse_run_id"] = parse_run_id
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_=conflict_set,
            )
            updated_existing += 1
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            inserted += 1
            existing_ids.add(v.id)
        session.execute(stmt)
    _LOG.info(
        "Сохранение вакансий завершено за %.1f с: +%s новых, %s привязано к прогону",
        time.monotonic() - started,
        inserted,
        updated_existing,
    )
    record_vacancy_upsert(inserted=inserted, updated_existing=updated_existing)
    if inserted or updated_existing:
        invalidate_vacancy_count_cache()
    return VacancyUpsertStats(inserted=inserted, updated_existing=updated_existing)


def archive_expired_vacancies(
    session: Session,
    *,
    now: datetime | None = None,
    ttl_days: int | None = None,
) -> int:
    """Архивировать (soft delete) вакансии старше TTL суток (post_date или parsed_at)."""
    ttl = ttl_days if ttl_days is not None else _vacancy_ttl_days()
    if ttl <= 0:
        return 0
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=ttl)
    result = session.execute(
        update(VacancyRow)
        .where(
            VacancyRow.archived_at.is_(None),
            or_(
                VacancyRow.post_date <= cutoff,
                and_(VacancyRow.post_date.is_(None), VacancyRow.parsed_at <= cutoff),
            ),
        )
        .values(archived_at=now),
        execution_options={"synchronize_session": "fetch"},
    )
    archived = result.rowcount or 0
    if archived:
        _LOG.info(
            "TTL вакансий: архивировано %s записей старше %s",
            archived,
            cutoff.isoformat(),
        )
        record_vacancies_archived(archived)
        invalidate_vacancy_count_cache()
    return archived


def _list_text_defer():
    return (defer(VacancyRow.raw_text), defer(VacancyRow.cleaned_text))


def list_vacancies(session: Session, query: VacancyQuery) -> tuple[list[VacancyListItem], int]:
    dialect = session.get_bind().dialect.name
    offset = (query.page - 1) * query.page_size
    order = [*_vacancy_order_by(query, dialect=dialect), VacancyRow.id.asc()]
    preview = vacancy_preview_expr()
    if query.resume_id is not None:
        stmt = (
            apply_vacancy_filters(select(VacancyRow, VacancyMatchRow, preview), query, dialect=dialect)
            .outerjoin(
                VacancyMatchRow,
                and_(
                    VacancyMatchRow.vacancy_id == VacancyRow.id,
                    VacancyMatchRow.resume_id == query.resume_id,
                ),
            )
            .options(*_list_text_defer())
        )
        rows = session.execute(stmt.order_by(*order).offset(offset).limit(query.page_size)).all()
        items: list[VacancyListItem] = []
        for vrow, mrow, prev in rows:
            items.append(
                row_to_list_item(
                    vrow,
                    prev or "",
                    match_score=mrow.overall_score if mrow is not None else None,
                    match_explanation=mrow.explanation if mrow is not None else None,
                )
            )
        # На первой неполной странице total = len (без отдельного COUNT).
        if query.page == 1 and len(items) < query.page_size:
            return items, len(items)
        return items, counted_vacancies(session, query, dialect=dialect)
    stmt = apply_vacancy_filters(select(VacancyRow, preview), query, dialect=dialect).options(*_list_text_defer())
    rows = session.execute(stmt.order_by(*order).offset(offset).limit(query.page_size)).all()
    items = [row_to_list_item(vrow, prev or "") for vrow, prev in rows]
    if query.page == 1 and len(items) < query.page_size:
        return items, len(items)
    return items, counted_vacancies(session, query, dialect=dialect)


def get_vacancy(session: Session, vacancy_id: str) -> Vacancy | None:
    row = session.get(VacancyRow, vacancy_id)
    if row is None:
        return None
    return row_to_vacancy(row)


def list_run_vacancies(
    session: Session,
    run_id: uuid.UUID,
    query: VacancyQuery,
) -> tuple[list[VacancyListItem], int]:
    conditions = [
        VacancyRow.last_parse_run_id == run_id,
        VacancyRow.is_duplicate.is_(False),
        VacancyRow.archived_at.is_(None),
    ]
    total = int(session.scalar(select(func.count(VacancyRow.id)).where(*conditions)) or 0)
    dialect = session.get_bind().dialect.name
    offset = (query.page - 1) * query.page_size
    preview = vacancy_preview_expr()
    stmt = (
        select(VacancyRow, preview)
        .where(*conditions)
        .options(*_list_text_defer())
        .order_by(*_vacancy_order_by(query, dialect=dialect), VacancyRow.id.asc())
        .offset(offset)
        .limit(query.page_size)
    )
    rows = session.execute(stmt).all()
    return [row_to_list_item(vrow, prev or "") for vrow, prev in rows], total


def _iter_full_vacancies(session: Session, query: VacancyQuery) -> list[Vacancy]:
    """Полные Vacancy по фильтрам (для export) — с raw/cleaned_text."""
    dialect = session.get_bind().dialect.name
    page_size = min(query.page_size, 500)
    page = 1
    out: list[Vacancy] = []
    while True:
        offset = (page - 1) * page_size
        order = [*_vacancy_order_by(query, dialect=dialect), VacancyRow.id.asc()]
        stmt = apply_vacancy_filters(select(VacancyRow), query, dialect=dialect)
        rows = session.scalars(stmt.order_by(*order).offset(offset).limit(page_size)).all()
        if not rows:
            break
        out.extend(row_to_vacancy(r) for r in rows)
        if len(rows) < page_size:
            break
        page += 1
    return out


def list_all_vacancies(session: Session, query: VacancyQuery) -> list[Vacancy]:
    """Все вакансии по фильтрам (полные тексты — для Excel/JSON export)."""
    return _iter_full_vacancies(session, query)


def list_all_run_vacancies(session: Session, run_id: uuid.UUID, query: VacancyQuery) -> list[Vacancy]:
    page_size = min(query.page_size, 500)
    page = 1
    out: list[Vacancy] = []
    dialect = session.get_bind().dialect.name
    conditions = [
        VacancyRow.last_parse_run_id == run_id,
        VacancyRow.is_duplicate.is_(False),
        VacancyRow.archived_at.is_(None),
    ]
    while True:
        offset = (page - 1) * page_size
        rows = session.scalars(
            select(VacancyRow)
            .where(*conditions)
            .order_by(*_vacancy_order_by(query, dialect=dialect), VacancyRow.id.asc())
            .offset(offset)
            .limit(page_size)
        ).all()
        if not rows:
            break
        out.extend(row_to_vacancy(r) for r in rows)
        if len(rows) < page_size:
            break
        page += 1
    return out


def get_vacancy_row(session: Session, vacancy_id: str) -> VacancyRow | None:
    return session.get(VacancyRow, vacancy_id)
