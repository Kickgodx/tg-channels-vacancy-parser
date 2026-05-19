"""Репозиторий PostgreSQL."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy

from backend.fx.apply import apply_salary_rub_for_vacancy, preload_fx_rates_for_vacancies
from backend.db.models import ParseRunRow, VacancyRow
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import FxConfig
from backend.db.vacancy_filters import apply_vacancy_filters, vacancy_count_stmt
from backend.db.vacancy_mapper import row_to_vacancy, vacancy_to_row
from backend.schemas import RunStatus, VacancyQuery, VacancySortField

@dataclass(frozen=True)
class VacancyUpsertStats:
    inserted: int = 0
    updated_existing: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated_existing


_LOG = logging.getLogger(__name__)

_SORT_COLUMNS: dict[VacancySortField, object] = {
    "post_date": VacancyRow.post_date,
    "detected_salary_avg": VacancyRow.detected_salary_avg,
    "detected_salary_avg_rub": VacancyRow.detected_salary_avg_rub,
    "work_format": VacancyRow.work_format,
    "detected_grade": VacancyRow.detected_grade,
    "salary_currency": VacancyRow.salary_currency,
    "vacancy_score": VacancyRow.vacancy_score,
}


def _vacancy_order_by(query: VacancyQuery):
    col = _SORT_COLUMNS.get(query.sort_by, VacancyRow.post_date)
    if query.sort_order == "asc":
        return col.asc().nulls_last()
    return col.desc().nulls_last()


def create_parse_run(session: Session, *, config_snapshot: dict) -> ParseRunRow:
    row = ParseRunRow(status="pending", config_snapshot=config_snapshot)
    session.add(row)
    session.flush()
    return row


def get_parse_run(session: Session, run_id: uuid.UUID) -> ParseRunRow | None:
    return session.get(ParseRunRow, run_id)


def list_parse_runs(session: Session, *, limit: int = 50, offset: int = 0) -> list[ParseRunRow]:
    stmt = (
        select(ParseRunRow)
        .order_by(ParseRunRow.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
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
        row.started_at = datetime.now(timezone.utc)
    if status in ("completed", "cancelled", "failed"):
        row.finished_at = datetime.now(timezone.utc)
    if error_message is not None:
        row.error_message = error_message


def finalize_parse_run(session: Session, run_id: uuid.UUID, result: ParseResult, *, status: str = "completed") -> None:
    """Сохранить метаданные прогона (отдельно от вакансий — чтобы статус не зависал при ошибке UPSERT)."""
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.status = status
    row.finished_at = datetime.now(timezone.utc)
    row.posts_fetched = result.posts_fetched
    row.parse_warnings = result.parse_warnings
    row.run_params = result.run_params
    row.metrics = result.metrics.model_dump(mode="json")


def save_parse_result(session: Session, run_id: uuid.UUID, result: ParseResult) -> None:
    finalize_parse_run(session, run_id, result, status="completed")
    upsert_vacancies(session, result.vacancies_unique, parse_run_id=run_id)


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
    parse_run_id: uuid.UUID,
    fx_config: FxConfig | None = None,
) -> VacancyUpsertStats:
    """
    Вставка уникальных вакансий по ``id`` (content hash).

    При совпадении текста обновляются только ``last_parse_run_id`` и ``updated_at``.
    """
    if not vacancies:
        return VacancyUpsertStats()
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    ids = [v.id for v in vacancies]
    existing_ids = set(
        session.scalars(select(VacancyRow.id).where(VacancyRow.id.in_(ids))).all()
    )
    fx = _fx_config(fx_config)
    _LOG.info("Сохранение %s вакансий в БД (уже есть %s)…", len(vacancies), len(existing_ids))
    rates_cache = preload_fx_rates_for_vacancies(session, vacancies, fx)
    if rates_cache:
        _LOG.info("Курсы для сохранения: %s дат в кэше", len(rates_cache))
    inserted = 0
    updated_existing = 0
    for v in vacancies:
        is_existing = v.id in existing_ids
        apply_salary_rub_for_vacancy(
            v, session, fx, rates_cache=rates_cache, force=is_existing
        )
        row = vacancy_to_row(v, parse_run_id=parse_run_id)
        data = {c.name: getattr(row, c.name) for c in VacancyRow.__table__.columns}
        data.pop("updated_at", None)
        if not data.get("parsed_at"):
            data["parsed_at"] = now
        stmt = insert(VacancyRow).values(**data)
        if is_existing:
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "last_parse_run_id": parse_run_id,
                    "updated_at": now,
                    "detected_salary_avg_rub": data["detected_salary_avg_rub"],
                },
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
    return VacancyUpsertStats(inserted=inserted, updated_existing=updated_existing)


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


def list_vacancies(session: Session, query: VacancyQuery) -> tuple[list[Vacancy], int]:
    dialect = session.get_bind().dialect.name
    stmt = apply_vacancy_filters(select(VacancyRow), query, dialect=dialect)
    total = int(session.scalar(vacancy_count_stmt(query, dialect=dialect)) or 0)
    offset = (query.page - 1) * query.page_size
    rows = session.scalars(
        stmt.order_by(_vacancy_order_by(query), VacancyRow.id.asc())
        .offset(offset)
        .limit(query.page_size)
    ).all()
    return [row_to_vacancy(r) for r in rows], total


def list_run_vacancies(
    session: Session,
    run_id: uuid.UUID,
    query: VacancyQuery,
) -> tuple[list[Vacancy], int]:
    conditions = [
        VacancyRow.last_parse_run_id == run_id,
        VacancyRow.is_duplicate.is_(False),
    ]
    stmt = select(VacancyRow).where(*conditions)
    total = int(
        session.scalar(select(func.count(VacancyRow.id)).where(*conditions)) or 0
    )
    offset = (query.page - 1) * query.page_size
    rows = session.scalars(
        stmt.order_by(_vacancy_order_by(query), VacancyRow.id.asc())
        .offset(offset)
        .limit(query.page_size)
    ).all()
    return [row_to_vacancy(r) for r in rows], total


def list_all_vacancies(session: Session, query: VacancyQuery) -> list[Vacancy]:
    """Все вакансии по фильтрам (пагинация по 500 — лимит VacancyQuery)."""
    page_size = min(query.page_size, 500)
    page = 1
    out: list[Vacancy] = []
    while True:
        batch, total = list_vacancies(
            session,
            query.model_copy(update={"page": page, "page_size": page_size}),
        )
        out.extend(batch)
        if not batch or len(out) >= total:
            break
        page += 1
    return out


def list_all_run_vacancies(session: Session, run_id: uuid.UUID, query: VacancyQuery) -> list[Vacancy]:
    page_size = min(query.page_size, 500)
    page = 1
    out: list[Vacancy] = []
    while True:
        batch, total = list_run_vacancies(
            session,
            run_id,
            query.model_copy(update={"page": page, "page_size": page_size}),
        )
        out.extend(batch)
        if not batch or len(out) >= total:
            break
        page += 1
    return out


def metrics_from_run(row: ParseRunRow) -> MetricsSummary | None:
    if not row.metrics:
        return None
    return MetricsSummary.model_validate(row.metrics)
