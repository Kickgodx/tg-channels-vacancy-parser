"""Репозиторий прогонов парсинга (parse_runs)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
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


