"""Репозиторий прогонов парсинга (parse_runs)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.db.models import ParseRunRow
from backend.db.vacancy_repository import (
    VacancyUpsertStats,
    archive_expired_vacancies,
    upsert_vacancies,
)
from backend.schemas import RunStatus
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.parse_result import ParseResult

_ACTIVE_PARSE_STATUSES = frozenset({"queued", "running"})


def create_parse_run(session: Session, *, config_snapshot: dict, status: str = "queued") -> ParseRunRow:
    row = ParseRunRow(status=status, config_snapshot=config_snapshot)
    session.add(row)
    session.flush()
    return row


def has_active_parse_run(session: Session) -> bool:
    n = session.scalar(
        select(func.count()).select_from(ParseRunRow).where(ParseRunRow.status.in_(_ACTIVE_PARSE_STATUSES))
    )
    return int(n or 0) > 0


def claim_parse_run(session: Session, run_id: uuid.UUID) -> ParseRunRow | None:
    now = datetime.now(UTC)
    result = session.execute(
        update(ParseRunRow)
        .where(ParseRunRow.id == run_id, ParseRunRow.status == "queued")
        .values(status="running", started_at=now)
    )
    if (result.rowcount or 0) != 1:
        return None
    session.flush()
    return session.get(ParseRunRow, run_id)


def patch_run_progress(session: Session, run_id: uuid.UUID, progress: dict) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.progress = progress
    row.progress_updated_at = datetime.now(UTC)
    session.flush()


def request_parse_run_cancel(session: Session, run_id: uuid.UUID) -> ParseRunRow | None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return None
    if row.status in ("completed", "failed", "cancelled"):
        return None
    row.cancel_requested = True
    if row.status == "queued":
        row.status = "cancelled"
        row.finished_at = datetime.now(UTC)
    session.flush()
    return row


def is_parse_run_cancel_requested(session: Session, run_id: uuid.UUID) -> bool:
    row = session.get(ParseRunRow, run_id)
    return bool(row and row.cancel_requested)


def set_parse_run_rq_job_id(session: Session, run_id: uuid.UUID, rq_job_id: str | None) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.rq_job_id = rq_job_id
    session.flush()


def reset_parse_run_to_queued(session: Session, run_id: uuid.UUID) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.status = "queued"
    row.started_at = None
    row.finished_at = None
    row.rq_job_id = None
    row.error_message = None
    if row.cancel_requested:
        row.status = "cancelled"
        row.finished_at = datetime.now(UTC)
        return
    session.flush()


def list_parse_runs_by_status(
    session: Session, status: str, *, older_than: datetime | None = None
) -> list[ParseRunRow]:
    stmt = select(ParseRunRow).where(ParseRunRow.status == status)
    if older_than is not None:
        if status == "running":
            heartbeat = func.coalesce(ParseRunRow.progress_updated_at, ParseRunRow.started_at, ParseRunRow.created_at)
            stmt = stmt.where(heartbeat < older_than)
        else:
            stmt = stmt.where(ParseRunRow.created_at < older_than)
    return list(session.scalars(stmt))


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


def metrics_from_run(row: ParseRunRow) -> MetricsSummary | None:
    if not row.metrics:
        return None
    return MetricsSummary.model_validate(row.metrics)
