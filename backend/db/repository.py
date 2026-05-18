"""Репозиторий PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy

from backend.db.models import ParseRunRow, VacancyRow
from backend.db.vacancy_filters import apply_vacancy_filters, vacancy_count_stmt
from backend.db.vacancy_mapper import row_to_vacancy, vacancy_to_row
from backend.schemas import RunStatus, VacancyQuery, VacancySortField

_SORT_COLUMNS: dict[VacancySortField, object] = {
    "post_date": VacancyRow.post_date,
    "detected_salary_avg": VacancyRow.detected_salary_avg,
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


def upsert_vacancies(
    session: Session,
    vacancies: list[Vacancy],
    *,
    parse_run_id: uuid.UUID,
) -> int:
    if not vacancies:
        return 0
    now = datetime.now(timezone.utc)
    count = 0
    for v in vacancies:
        row = vacancy_to_row(v, parse_run_id=parse_run_id)
        data = {c.name: getattr(row, c.name) for c in VacancyRow.__table__.columns}
        # updated_at NOT NULL — не передаём None; на INSERT сработает server_default
        data.pop("updated_at", None)
        if not data.get("parsed_at"):
            data["parsed_at"] = now
        stmt = insert(VacancyRow).values(**data)
        update_cols = {k: val for k, val in data.items() if k != "id"}
        update_cols["updated_at"] = now
        stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_cols)
        session.execute(stmt)
        count += 1
    return count


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
    stmt = select(VacancyRow).where(
        VacancyRow.last_parse_run_id == run_id,
        VacancyRow.is_duplicate.is_(False),
    )
    total = session.scalar(select(func.count(VacancyRow.id)).where(*stmt._where_criteria)) or 0
    offset = (query.page - 1) * query.page_size
    rows = session.scalars(
        stmt.order_by(_vacancy_order_by(query), VacancyRow.id.asc()).offset(offset).limit(query.page_size)
    ).all()
    return [row_to_vacancy(r) for r in rows], int(total)


def metrics_from_run(row: ParseRunRow) -> MetricsSummary | None:
    if not row.metrics:
        return None
    return MetricsSummary.model_validate(row.metrics)
