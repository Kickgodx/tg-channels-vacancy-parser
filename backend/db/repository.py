"""Репозиторий PostgreSQL."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock

from sqlalchemy import and_, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer

from backend.db.models import (
    CoverLetterRow,
    LlmJobRow,
    ParseRunRow,
    ResumeRow,
    SavedFilterRow,
    UserRow,
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
from backend.schemas import RunStatus, VacancyListItem, VacancyQuery, VacancySortField
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import FxConfig
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.parse_result import ParseResult
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


def create_parse_run(session: Session, *, config_snapshot: dict) -> ParseRunRow:
    row = ParseRunRow(status="pending", config_snapshot=config_snapshot)
    session.add(row)
    session.flush()
    return row


def get_parse_run(session: Session, run_id: uuid.UUID) -> ParseRunRow | None:
    return session.get(ParseRunRow, run_id)


def list_parse_runs(session: Session, *, limit: int = 50, offset: int = 0) -> list[ParseRunRow]:
    stmt = select(ParseRunRow).order_by(ParseRunRow.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


def set_run_status(
    session: Session,
    run_id: uuid.UUID,
    status: RunStatus,
    *,
    error_message: str | None = None,
) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.status = status
    if status == "running" and row.started_at is None:
        row.started_at = datetime.now(UTC)
    if status in ("completed", "cancelled", "failed"):
        row.finished_at = datetime.now(UTC)
    if error_message is not None:
        row.error_message = error_message


def finalize_parse_run(
    session: Session,
    run_id: uuid.UUID,
    result: ParseResult,
    *,
    status: str = "completed",
) -> None:
    """Сохранить метаданные прогона (отдельно от вакансий — чтобы статус не зависал при ошибке UPSERT)."""
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.status = status
    row.finished_at = datetime.now(UTC)
    row.posts_fetched = result.posts_fetched
    row.parse_warnings = result.parse_warnings
    row.run_params = result.run_params
    row.metrics = result.metrics.model_dump(mode="json")


def save_parse_result(session: Session, run_id: uuid.UUID, result: ParseResult) -> None:
    finalize_parse_run(session, run_id, result, status="completed")
    upsert_vacancies(session, result.vacancies_unique, parse_run_id=run_id)
    archive_expired_vacancies(session)


def save_parse_cancelled(
    session: Session,
    run_id: uuid.UUID,
    result: ParseResult | None,
    *,
    status: RunStatus = "cancelled",
) -> None:
    if result is not None:
        finalize_parse_run(session, run_id, result, status=status)
        upsert_vacancies(session, result.vacancies_unique, parse_run_id=run_id)
        archive_expired_vacancies(session)
    else:
        set_run_status(session, run_id, status)


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

    При совпадении текста обновляются только ``last_parse_run_id`` и ``updated_at``.
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
            conflict_set = {
                "updated_at": now,
                "detected_salary_avg_rub": data["detected_salary_avg_rub"],
                "ai_notes": data["ai_notes"],
                "ai_confidence": data["ai_confidence"],
                "extraction_source": data["extraction_source"],
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


def patch_run_vacancy_upsert_stats(
    session: Session,
    run_id: uuid.UUID,
    stats: VacancyUpsertStats,
) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    params = dict(row.run_params or {})
    params["vacancies_inserted"] = stats.inserted
    params["vacancies_existing"] = stats.updated_existing
    row.run_params = params


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


def metrics_from_run(row: ParseRunRow) -> MetricsSummary | None:
    if not row.metrics:
        return None
    return MetricsSummary.model_validate(row.metrics)


def get_user(session: Session, user_id: int) -> UserRow | None:
    return session.get(UserRow, user_id)


def get_user_by_email(session: Session, email: str) -> UserRow | None:
    return session.scalar(select(UserRow).where(UserRow.email == email.lower()))


def count_users(session: Session) -> int:
    return int(session.scalar(select(func.count(UserRow.id))) or 0)


def create_user(session: Session, *, email: str, password_hash: str, is_admin: bool = False) -> UserRow:
    row = UserRow(
        email=email.lower(),
        password_hash=password_hash,
        is_admin=is_admin,
    )
    session.add(row)
    session.flush()
    return row


def claim_legacy_rows(session: Session, user_id: int) -> None:
    """Привязать ещё ничьи строки (user_id IS NULL) к пользователю — backfill миграции 007."""
    for model in (ResumeRow, SavedFilterRow):
        session.execute(update(model).where(model.user_id.is_(None)).values(user_id=user_id))


def list_saved_filters(session: Session, user_id: int | None = None) -> list[SavedFilterRow]:
    """user_id=None — без скоупа (тесты/служебные вызовы); роуты всегда передают id."""
    stmt = select(SavedFilterRow).order_by(SavedFilterRow.name.asc())
    if user_id is not None:
        stmt = stmt.where(SavedFilterRow.user_id == user_id)
    return list(session.scalars(stmt))


def create_saved_filter(session: Session, *, name: str, params: dict, user_id: int | None = None) -> SavedFilterRow:
    row = SavedFilterRow(name=name, params=params, user_id=user_id)
    session.add(row)
    session.flush()
    return row


def delete_saved_filter(session: Session, filter_id: int, user_id: int | None = None) -> bool:
    row = session.get(SavedFilterRow, filter_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return False
    session.delete(row)
    session.flush()
    return True


def list_resumes(session: Session, user_id: int | None = None) -> list[ResumeRow]:
    stmt = select(ResumeRow).order_by(ResumeRow.created_at.desc(), ResumeRow.id.desc())
    if user_id is not None:
        stmt = stmt.where(ResumeRow.user_id == user_id)
    return list(session.scalars(stmt))


def get_resume(session: Session, resume_id: int) -> ResumeRow | None:
    return session.get(ResumeRow, resume_id)


def create_resume(
    session: Session,
    *,
    name: str,
    text: str,
    skills: list | None = None,
    user_id: int | None = None,
) -> ResumeRow:
    row = ResumeRow(name=name, text=text, skills_json=skills, user_id=user_id)
    session.add(row)
    session.flush()
    return row


def update_resume(
    session: Session,
    resume_id: int,
    *,
    user_id: int | None = None,
    name: str | None = None,
    text: str | None = None,
) -> ResumeRow | None:
    row = session.get(ResumeRow, resume_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return None
    if name is not None:
        row.name = name
    if text is not None:
        row.text = text
    session.flush()
    return row


def delete_resume(session: Session, resume_id: int, user_id: int | None = None) -> bool:
    row = session.get(ResumeRow, resume_id)
    if row is None or (user_id is not None and row.user_id != user_id):
        return False
    session.query(VacancyMatchRow).filter(VacancyMatchRow.resume_id == resume_id).delete()
    session.query(CoverLetterRow).filter(CoverLetterRow.resume_id == resume_id).delete()
    session.delete(row)
    session.flush()
    return True


def get_vacancy_row(session: Session, vacancy_id: str) -> VacancyRow | None:
    return session.get(VacancyRow, vacancy_id)


def get_vacancy_match(session: Session, *, resume_id: int, vacancy_id: str) -> VacancyMatchRow | None:
    return session.get(VacancyMatchRow, (resume_id, vacancy_id))


def upsert_vacancy_match(
    session: Session,
    *,
    resume_id: int,
    vacancy_id: str,
    overall_score: int,
    confidence: int,
    breakdown: dict | None,
    explanation: str | None,
    matching_skills: list | None,
    missing_skills: list | None,
    strengths: list | None,
    risks: list | None,
    model: str,
) -> VacancyMatchRow:
    """Записать/обновить оценку пары (resume, vacancy) — кэш матчинга по PK."""
    row = session.get(VacancyMatchRow, (resume_id, vacancy_id))
    if row is None:
        row = VacancyMatchRow(
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            overall_score=overall_score,
            confidence=confidence,
            breakdown=breakdown,
            explanation=explanation,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            strengths=strengths,
            risks=risks,
            model=model,
        )
        session.add(row)
    else:
        row.overall_score = overall_score
        row.confidence = confidence
        row.breakdown = breakdown
        row.explanation = explanation
        row.matching_skills = matching_skills
        row.missing_skills = missing_skills
        row.strengths = strengths
        row.risks = risks
        row.model = model
        row.created_at = datetime.now(UTC)
    session.flush()
    return row


def get_cover_letter(session: Session, *, resume_id: int, vacancy_id: str) -> CoverLetterRow | None:
    return session.get(CoverLetterRow, (resume_id, vacancy_id))


def upsert_cover_letter(
    session: Session,
    *,
    resume_id: int,
    vacancy_id: str,
    text: str,
    tone: str,
    model: str,
) -> CoverLetterRow:
    """Записать/обновить письмо пары (resume, vacancy) — кэш генерации по PK."""
    row = session.get(CoverLetterRow, (resume_id, vacancy_id))
    if row is None:
        row = CoverLetterRow(
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            text=text,
            tone=tone,
            model=model,
        )
        session.add(row)
    else:
        row.text = text
        row.tone = tone
        row.model = model
        row.created_at = datetime.now(UTC)
    session.flush()
    return row


def delete_cover_letter(session: Session, *, resume_id: int, vacancy_id: str) -> bool:
    row = session.get(CoverLetterRow, (resume_id, vacancy_id))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


_ACTIVE_LLM_STATUSES = ("queued", "running")


def create_llm_job(
    session: Session,
    *,
    user_id: int,
    kind: str,
    resume_id: int,
    vacancy_id: str,
    force: bool = False,
    params: dict | None = None,
) -> LlmJobRow:
    row = LlmJobRow(
        user_id=user_id,
        kind=kind,
        resume_id=resume_id,
        vacancy_id=vacancy_id,
        status="queued",
        force=force,
        params=params,
    )
    session.add(row)
    session.flush()
    return row


def get_llm_job(session: Session, job_id: uuid.UUID) -> LlmJobRow | None:
    return session.get(LlmJobRow, job_id)


def set_llm_job_rq_job_id(
    session: Session,
    job_id: uuid.UUID,
    rq_job_id: str | None,
) -> LlmJobRow | None:
    row = session.get(LlmJobRow, job_id)
    if row is None:
        return None
    row.rq_job_id = rq_job_id
    session.flush()
    return row


def claim_llm_job(session: Session, job_id: uuid.UUID) -> LlmJobRow | None:
    """Атомарно ``queued`` → ``running``. Только победитель получает строку."""
    now = datetime.now(UTC)
    result = session.execute(
        update(LlmJobRow)
        .where(LlmJobRow.id == job_id, LlmJobRow.status == "queued")
        .values(status="running", started_at=now)
    )
    if (result.rowcount or 0) != 1:
        return None
    session.flush()
    return session.get(LlmJobRow, job_id)


def find_active_llm_job(
    session: Session,
    *,
    user_id: int,
    kind: str,
    resume_id: int,
    vacancy_id: str,
) -> LlmJobRow | None:
    return session.scalar(
        select(LlmJobRow)
        .where(
            LlmJobRow.user_id == user_id,
            LlmJobRow.kind == kind,
            LlmJobRow.resume_id == resume_id,
            LlmJobRow.vacancy_id == vacancy_id,
            LlmJobRow.status.in_(_ACTIVE_LLM_STATUSES),
        )
        .order_by(LlmJobRow.created_at.desc())
        .limit(1)
    )


def count_active_llm_jobs(session: Session, *, user_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(LlmJobRow)
            .where(
                LlmJobRow.user_id == user_id,
                LlmJobRow.status.in_(_ACTIVE_LLM_STATUSES),
            )
        )
        or 0
    )


def set_llm_job_status(
    session: Session,
    job_id: uuid.UUID,
    status: str,
    *,
    error_message: str | None = None,
) -> LlmJobRow | None:
    row = session.get(LlmJobRow, job_id)
    if row is None:
        return None
    now = datetime.now(UTC)
    row.status = status
    if status == "running" and row.started_at is None:
        row.started_at = now
    if status in ("done", "error"):
        row.finished_at = now
        if error_message is not None:
            row.error_message = error_message
    session.flush()
    return row


def list_llm_jobs_by_status(
    session: Session,
    *,
    status: str,
    older_than: datetime | None = None,
) -> list[LlmJobRow]:
    stmt = select(LlmJobRow).where(LlmJobRow.status == status)
    if older_than is not None:
        if status == "running":
            stmt = stmt.where(LlmJobRow.started_at.is_not(None), LlmJobRow.started_at < older_than)
        else:
            stmt = stmt.where(LlmJobRow.created_at < older_than)
    return list(session.scalars(stmt).all())


def reset_llm_job_to_queued(session: Session, job_id: uuid.UUID) -> LlmJobRow | None:
    """Вернуть зависшую задачу в queued (очистить finished/error/rq_job_id)."""
    row = session.get(LlmJobRow, job_id)
    if row is None:
        return None
    row.status = "queued"
    row.started_at = None
    row.finished_at = None
    row.error_message = None
    row.rq_job_id = None
    session.flush()
    return row


def count_llm_jobs_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(select(LlmJobRow.status, func.count()).group_by(LlmJobRow.status))
    return {str(status): int(n) for status, n in rows}
