"""Репозиторий вакансий (shared corpus + upsert / list / TTL)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import and_, case, false, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer

from backend.db.aggregate_cache import invalidate_aggregate_cache
from backend.db.models import (
    ParseRunRow,
    ParseRunVacancyRow,
    VacancyMatchRow,
    VacancyRow,
    VacancySightingRow,
)
from backend.db.vacancy_filters import apply_vacancy_filters, fts_query, vacancy_count_stmt
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
from backend.services.llm_result_freshness import match_is_stale
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import AiConfig, FxConfig
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


def _vacancy_count_cache_key(query: VacancyQuery) -> str:
    payload = query.model_dump(exclude={"page", "page_size", "sort_by", "sort_order"})
    return repr(sorted(payload.items()))


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


# Ключ включает свободный текст ``q``: без вытеснения кэш рос бы на каждый новый поиск.
_COUNT_CACHE_MAX_ENTRIES = 256


def _set_cached_vacancy_count(key: str, total: int) -> None:
    with _count_cache_lock:
        now = time.monotonic()
        if len(_count_cache) >= _COUNT_CACHE_MAX_ENTRIES:
            for k in [k for k, (ts, _) in _count_cache.items() if now - ts > _COUNT_CACHE_TTL_S]:
                del _count_cache[k]
            if len(_count_cache) >= _COUNT_CACHE_MAX_ENTRIES:
                _count_cache.clear()
        _count_cache[key] = (now, total)


def invalidate_vacancy_count_cache() -> None:
    """Сбросить кэши чтения корпуса в этом процессе: COUNT списка и агрегаты аналитики."""
    with _count_cache_lock:
        _count_cache.clear()
    invalidate_aggregate_cache()


def counted_vacancies(session: Session, query: VacancyQuery) -> int:
    key = _vacancy_count_cache_key(query)
    cached = _get_cached_vacancy_count(key)
    if cached is not None:
        return cached
    total = int(session.scalar(vacancy_count_stmt(query)) or 0)
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


def _vacancy_order_by(query: VacancyQuery) -> list:
    if query.sort_by == "match_score" and query.resume_id is not None:
        col = VacancyMatchRow.overall_score
    else:
        col = _SORT_COLUMNS.get(query.sort_by, VacancyRow.post_date)
    order = col.asc().nulls_last() if query.sort_order == "asc" else col.desc().nulls_last()
    if query.q:
        return [func.ts_rank(*fts_query(VacancyRow.cleaned_text, query.q)).desc(), order]
    return [order]


def _fx_config(fx_config: FxConfig | None) -> FxConfig:
    if fx_config is not None:
        return fx_config
    return load_config(get_settings().config_path).fx


# Строк на один INSERT: ~40 колонок × 200 = 8 000 параметров — с запасом ниже лимита Postgres (65 535).
_UPSERT_CHUNK = 200

# Результат разбора поста: обновляется только целиком, чтобы строка не смешивала два
# разбора (грейд от старой модели, а extraction_source — от новой). RUB-зарплата — здесь
# же: она считается из detected_salary_* нового разбора. AI поднимает vacancy_score до
# порога, поэтому баллы — тоже часть разбора.
_EXTRACTION_COLUMNS = (
    "detected_specializations",
    "detected_grade",
    "detected_salary_min",
    "detected_salary_max",
    "detected_salary_avg",
    "detected_salary_avg_rub",
    "salary_currency",
    "work_format",
    "country",
    "city",
    "company",
    "contact",
    "technologies",
    "required_experience",
    "employment_type",
    "application_urls",
    "extraction_source",
    "ai_confidence",
    "ai_notes",
    "vacancy_score",
    "vacancy_substantive_score",
)


def upsert_vacancies(
    session: Session,
    vacancies: list[Vacancy],
    *,
    parse_run_id: uuid.UUID | None,
    fx_config: FxConfig | None = None,
) -> VacancyUpsertStats:
    """
    Вставка вакансий по ``id`` (content hash) — пакетами, по INSERT на чанк.

    При совпадении текста обновляются ``last_parse_run_id``, ``updated_at`` и результат
    разбора (``_EXTRACTION_COLUMNS``) — целиком, если новый разбор не хуже: от LLM
    (``ai``/``hybrid``) или поверх rules-разбора. Rules-разбор не затирает сохранённый
    LLM-разбор (прогон с выключенным AI не откатывает строку к regex). Если пост новее сохранённого
    (канал повторно опубликовал тот же текст), ``post_date`` сдвигается вперёд, а
    вакансия возвращается из архива — иначе TTL по первой публикации прятал бы её навсегда.
    Каждая вакансия пачки (новая и уже известная) записывается в ``parse_run_vacancies``
    этого прогона — по этой связке строятся список и экспорт прогона.
    ``parse_run_id=None`` — ручной импорт без прогона: существующая привязка
    к прогону при update не затирается. Повтор ``id`` внутри пачки — берётся последний.

    Дубли дедупа (``is_duplicate``) в ``vacancies`` не пишутся. В прогоне каждый пост
    пачки записывается в ``vacancy_sightings`` (см. ``_insert_sightings``).
    """
    if not vacancies:
        return VacancyUpsertStats()
    started = time.monotonic()
    now = datetime.now(UTC)
    by_id = {v.id: v for v in vacancies if not v.is_duplicate}
    existing_ids = set(session.scalars(select(VacancyRow.id).where(VacancyRow.id.in_(list(by_id)))).all())
    fx = _fx_config(fx_config)
    _LOG.info("Сохранение %s вакансий в БД (уже есть %s)…", len(by_id), len(existing_ids))
    rates_cache = preload_fx_rates_for_vacancies(session, list(by_id.values()), fx)
    if rates_cache:
        _LOG.info("Курсы для сохранения: %s дат в кэше", len(rates_cache))
    new_rows: list[dict] = []
    existing_rows: list[dict] = []
    for v in by_id.values():
        is_existing = v.id in existing_ids
        apply_salary_rub_for_vacancy(v, session, fx, rates_cache=rates_cache, force=is_existing)
        row = vacancy_to_row(v, parse_run_id=parse_run_id)
        data = {c.name: getattr(row, c.name) for c in VacancyRow.__table__.columns}
        data.pop("updated_at", None)
        if not data.get("parsed_at"):
            data["parsed_at"] = now
        (existing_rows if is_existing else new_rows).append(data)

    for chunk in _chunks(new_rows):
        session.execute(insert(VacancyRow).values(chunk).on_conflict_do_nothing(index_elements=["id"]))
    for chunk in _chunks(existing_rows):
        stmt = insert(VacancyRow).values(chunk)
        reposted_later = stmt.excluded.post_date > VacancyRow.post_date
        # SET в ON CONFLICT видит старую строку, так что extraction_source в условии — прежний.
        refresh_extraction = or_(
            stmt.excluded.extraction_source != "rules",
            VacancyRow.extraction_source == "rules",
        )
        table = VacancyRow.__table__.c
        conflict_set = {
            col: case((refresh_extraction, stmt.excluded[col]), else_=table[col]) for col in _EXTRACTION_COLUMNS
        }
        conflict_set |= {
            "updated_at": now,
            # GREATEST пропускает NULL: пустая дата заполняется, более ранний пост её не сдвигает.
            "post_date": func.greatest(VacancyRow.post_date, stmt.excluded.post_date),
            "archived_at": case((reposted_later, null()), else_=VacancyRow.archived_at),
        }
        if parse_run_id is not None:
            conflict_set["last_parse_run_id"] = parse_run_id
        session.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=conflict_set))
    if parse_run_id is not None:
        links = [{"parse_run_id": parse_run_id, "vacancy_id": vid} for vid in by_id]
        for chunk in _chunks(links):
            session.execute(insert(ParseRunVacancyRow).values(chunk).on_conflict_do_nothing())
        _insert_sightings(session, vacancies, existing_ids=existing_ids, parse_run_id=parse_run_id, now=now)

    inserted, updated_existing = len(new_rows), len(existing_rows)
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


def _insert_sightings(
    session: Session,
    vacancies: list[Vacancy],
    *,
    existing_ids: set[str],
    parse_run_id: uuid.UUID,
    now: datetime,
) -> None:
    """Появление каждого поста пачки: оригинал или дубль со ссылкой на каноническую вакансию.

    Оригинал — пост, из которого вакансия впервые попала в БД. Дубль — дубль дедупа
    внутри прогона или текст, уже бывший в БД до этой пачки (``seen_before``):
    другой канал, перепост в том же канале, прошлый прогон. Повторная вычитка того же
    поста (ретрай, пересечение окон) ничего не меняет — ON CONFLICT по ключу поста.
    """
    rows: list[dict] = []
    for v in vacancies:
        if v.is_duplicate:
            vacancy_id, reason = v.duplicate_of, v.duplicate_reason or "duplicate"
        elif v.id in existing_ids:
            vacancy_id, reason = v.id, "seen_before"
        else:
            vacancy_id, reason = v.id, None
        if not vacancy_id:
            continue
        rows.append(
            {
                "source_post_key": v.source_post_key[:128],
                "vacancy_id": vacancy_id,
                "source_channel": v.source_channel[:128],
                "post_date": v.post_date,
                "parse_run_id": parse_run_id,
                "is_duplicate": reason is not None,
                "duplicate_reason": reason[:64] if reason else None,
                "seen_at": now,
            }
        )
    # Каноническая вакансия дубля — в этой пачке или в прошлой. Если её нет (запись
    # прошлой пачки не удалась), появление пропускаем: FK уронил бы всю пачку.
    missing = {r["vacancy_id"] for r in rows} - {v.id for v in vacancies if not v.is_duplicate}
    if missing:
        absent = missing - set(session.scalars(select(VacancyRow.id).where(VacancyRow.id.in_(list(missing)))).all())
        rows = [r for r in rows if r["vacancy_id"] not in absent]
    for chunk in _chunks(rows):
        session.execute(insert(VacancySightingRow).values(chunk).on_conflict_do_nothing())


# HH и ручной импорт — не посты Telegram, у них нет появлений.
NON_TELEGRAM_CHANNELS = ("hh", "manual")


def backfill_original_sightings(session: Session) -> int:
    """Появление-оригинал для каждой вакансии Telegram, у поста которой его ещё нет.

    Для данных, записанных до ``vacancy_sightings`` (дамп старого формата).
    """
    src = (
        select(
            VacancyRow.source_post_key,
            VacancyRow.id,
            VacancyRow.source_channel,
            VacancyRow.post_date,
            ParseRunRow.id,
            false(),
            VacancyRow.parsed_at,
        )
        # last_parse_run_id — не FK: удалённый прогон превращается в NULL.
        .outerjoin(ParseRunRow, ParseRunRow.id == VacancyRow.last_parse_run_id)
        .where(VacancyRow.is_duplicate.is_(False), VacancyRow.source_channel.not_in(NON_TELEGRAM_CHANNELS))
    )
    stmt = (
        insert(VacancySightingRow)
        .from_select(
            [
                "source_post_key",
                "vacancy_id",
                "source_channel",
                "post_date",
                "parse_run_id",
                "is_duplicate",
                "seen_at",
            ],
            src,
        )
        .on_conflict_do_nothing()
    )
    # Для INSERT SQLAlchemy без preserve_rowcount отдаёт rowcount -1 (запоминает только UPDATE/DELETE).
    return int(session.execute(stmt.execution_options(preserve_rowcount=True)).rowcount or 0)


def _chunks(rows: list[dict]):
    for i in range(0, len(rows), _UPSERT_CHUNK):
        yield rows[i : i + _UPSERT_CHUNK]


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


def list_vacancies(
    session: Session,
    query: VacancyQuery,
    *,
    match_ai: AiConfig | None = None,
) -> tuple[list[VacancyListItem], int]:
    """Страница списка; с ``resume_id`` — плюс оценка пары.

    ``match_ai`` — текущая AI-конфигурация (её передаёт роут): по ней элемент получает
    ``match_stale``. Без неё ``match_stale`` не считается (None). На колонку оценки и
    сортировку устаревание не влияет.
    """
    offset = (query.page - 1) * query.page_size
    order = [*_vacancy_order_by(query), VacancyRow.id.asc()]
    preview = vacancy_preview_expr()
    if query.resume_id is not None:
        stmt = (
            apply_vacancy_filters(select(VacancyRow, VacancyMatchRow, preview), query)
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
                    match_stale=(match_is_stale(mrow, match_ai) if mrow is not None and match_ai is not None else None),
                )
            )
        # На первой неполной странице total = len (без отдельного COUNT).
        if query.page == 1 and len(items) < query.page_size:
            return items, len(items)
        return items, counted_vacancies(session, query)
    stmt = apply_vacancy_filters(select(VacancyRow, preview), query).options(*_list_text_defer())
    rows = session.execute(stmt.order_by(*order).offset(offset).limit(query.page_size)).all()
    items = [row_to_list_item(vrow, prev or "") for vrow, prev in rows]
    if query.page == 1 and len(items) < query.page_size:
        return items, len(items)
    return items, counted_vacancies(session, query)


def get_vacancy(session: Session, vacancy_id: str) -> Vacancy | None:
    row = session.get(VacancyRow, vacancy_id)
    if row is None:
        return None
    return row_to_vacancy(row)


def _run_conditions(run_id: uuid.UUID) -> list:
    # Связка, а не last_parse_run_id: тот перезаписывается следующим прогоном.
    # Архивные не скрываются: прогон показывает всё, что нашёл, а не то, что ещё актуально.
    run_vacancy_ids = select(ParseRunVacancyRow.vacancy_id).where(ParseRunVacancyRow.parse_run_id == run_id)
    return [
        VacancyRow.id.in_(run_vacancy_ids),
        VacancyRow.is_duplicate.is_(False),
    ]


def count_run_vacancies(session: Session, run_id: uuid.UUID) -> int:
    return int(session.scalar(select(func.count(VacancyRow.id)).where(*_run_conditions(run_id))) or 0)


def list_run_vacancies(
    session: Session,
    run_id: uuid.UUID,
    query: VacancyQuery,
) -> tuple[list[VacancyListItem], int]:
    total = count_run_vacancies(session, run_id)
    offset = (query.page - 1) * query.page_size
    preview = vacancy_preview_expr()
    stmt = (
        select(VacancyRow, preview)
        .where(*_run_conditions(run_id))
        .options(*_list_text_defer())
        .order_by(*_vacancy_order_by(query), VacancyRow.id.asc())
        .offset(offset)
        .limit(query.page_size)
    )
    rows = session.execute(stmt).all()
    return [row_to_list_item(vrow, prev or "") for vrow, prev in rows], total


# Строк за одно чтение курсора экспорта.
_EXPORT_YIELD_PER = 500


def _iter_rows(session: Session, stmt) -> Iterator[Vacancy]:
    """Один запрос, чтение порциями (на Postgres — server-side cursor), без OFFSET-цикла.

    Строку сразу выкидываем из identity map сессии — иначе ORM держал бы в памяти весь экспорт.
    """
    for row in session.scalars(stmt.execution_options(yield_per=_EXPORT_YIELD_PER)):
        vacancy = row_to_vacancy(row)
        session.expunge(row)
        yield vacancy


def iter_export_vacancies(session: Session, query: VacancyQuery, *, limit: int) -> Iterator[Vacancy]:
    """Полные Vacancy (с raw/cleaned_text) по фильтрам списка, не больше ``limit`` — для экспорта."""
    order = [*_vacancy_order_by(query), VacancyRow.id.asc()]
    stmt = apply_vacancy_filters(select(VacancyRow), query).order_by(*order).limit(limit)
    return _iter_rows(session, stmt)


def iter_export_run_vacancies(
    session: Session,
    run_id: uuid.UUID,
    query: VacancyQuery,
    *,
    limit: int,
) -> Iterator[Vacancy]:
    stmt = (
        select(VacancyRow)
        .where(*_run_conditions(run_id))
        .order_by(*_vacancy_order_by(query), VacancyRow.id.asc())
        .limit(limit)
    )
    return _iter_rows(session, stmt)


def get_vacancy_row(session: Session, vacancy_id: str) -> VacancyRow | None:
    return session.get(VacancyRow, vacancy_id)
