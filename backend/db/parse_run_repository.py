"""Репозиторий прогонов парсинга (parse_runs)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.db.models import ParseRunRow
from backend.db.vacancy_repository import (
    VacancyUpsertStats,
    archive_expired_vacancies,
    upsert_vacancies,
)
from backend.schemas import RunStatus
from backend.settings import get_settings
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.parse_result import ParseResult

_ACTIVE_PARSE_STATUSES = frozenset({"queued", "running"})
_TERMINAL_PARSE_STATUSES = frozenset({"completed", "cancelled", "failed"})


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


def _lease_deadline(now: datetime | None = None) -> datetime:
    base = now or datetime.now(UTC)
    seconds = max(30, get_settings().parse_job_lease_seconds)
    return base + timedelta(seconds=seconds)


def new_lease_owner(prefix: str = "w") -> str:
    """Уникальный fencing-token владельца lease (≤64)."""
    return f"{prefix}-{uuid.uuid4().hex}"[:64]


def claim_parse_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    lease_owner: str | None = None,
) -> ParseRunRow | None:
    now = datetime.now(UTC)
    owner = (lease_owner or new_lease_owner("parse")).strip()[:64] or new_lease_owner("parse")
    result = session.execute(
        update(ParseRunRow)
        .where(ParseRunRow.id == run_id, ParseRunRow.status == "queued")
        .values(
            status="running",
            started_at=now,
            lease_owner=owner,
            lease_until=_lease_deadline(now),
            attempts=ParseRunRow.attempts + 1,
        )
    )
    if (result.rowcount or 0) != 1:
        return None
    session.flush()
    return session.get(ParseRunRow, run_id)


def touch_parse_run_lease(
    session: Session,
    run_id: uuid.UUID,
    *,
    lease_owner: str,
) -> bool:
    """Продлить lease только если ``lease_owner`` совпадает (fencing)."""
    now = datetime.now(UTC)
    owner = lease_owner.strip()[:64]
    result = session.execute(
        update(ParseRunRow)
        .where(
            ParseRunRow.id == run_id,
            ParseRunRow.status == "running",
            ParseRunRow.lease_owner == owner,
        )
        .values(lease_until=_lease_deadline(now))
    )
    session.flush()
    return (result.rowcount or 0) == 1


def attempts_exhausted_message(attempts: int) -> str:
    return f"Превышено число попыток ({attempts}): воркер не довёл прогон до конца (падение процесса или потеря lease)."


def _reclaim_values(row: ParseRunRow, ts: datetime) -> tuple[str, dict]:
    """Во что перевести зависший ``running``: итоговый статус и значения UPDATE."""
    base = {"rq_job_id": None, "lease_owner": None, "lease_until": None}
    if row.cancel_requested:
        return "cancelled", {
            **base,
            "status": "cancelled",
            "finished_at": ts,
            "started_at": None,
            "error_message": None,
        }
    attempts = int(row.attempts or 0)
    if attempts >= max(1, get_settings().parse_job_max_attempts):
        # Отравленный прогон: каждый requeue заново вычитывает Telegram и платит за LLM.
        return "failed", {
            **base,
            "status": "failed",
            "finished_at": ts,
            "error_message": attempts_exhausted_message(attempts),
        }
    return "queued", {
        **base,
        "status": "queued",
        "started_at": None,
        "finished_at": None,
        "error_message": None,
    }


def reclaim_expired_parse_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> str | None:
    """Атомарно running(expired lease) → queued|cancelled|failed. None если не удалось.

    Возвращает итоговый статус: ``cancelled`` — была запрошена отмена, ``failed`` —
    исчерпан лимит попыток (``PARSE_JOB_MAX_ATTEMPTS``), иначе ``queued``.
    """
    ts = now or datetime.now(UTC)
    row = session.get(ParseRunRow, run_id)
    if row is None or row.status != "running":
        return None
    # legacy без lease_until: не reclaim здесь (recover отфильтрует по older_than)
    if row.lease_until is None:
        return None
    until = row.lease_until if row.lease_until.tzinfo else row.lease_until.replace(tzinfo=UTC)
    if until >= ts:
        return None

    outcome, values = _reclaim_values(row, ts)
    # synchronize_session=False: SQLite отдаёт naive datetime, evaluate ломается на aware ts.
    result = session.execute(
        update(ParseRunRow)
        .where(
            ParseRunRow.id == run_id,
            ParseRunRow.status == "running",
            ParseRunRow.lease_until.is_not(None),
            ParseRunRow.lease_until < ts,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.expire_all()
    return outcome if (result.rowcount or 0) == 1 else None


def reclaim_legacy_stale_parse_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    older_than: datetime,
) -> str | None:
    """Атомарно reclaim running без lease_until (legacy) по wall-clock."""
    row = session.get(ParseRunRow, run_id)
    if row is None or row.status != "running" or row.lease_until is not None:
        return None
    heartbeat = row.started_at or row.created_at
    if heartbeat is None:
        return None
    hb = heartbeat if heartbeat.tzinfo else heartbeat.replace(tzinfo=UTC)
    if hb >= older_than:
        return None
    outcome, values = _reclaim_values(row, datetime.now(UTC))
    result = session.execute(
        update(ParseRunRow)
        .where(
            ParseRunRow.id == run_id,
            ParseRunRow.status == "running",
            ParseRunRow.lease_until.is_(None),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.expire_all()
    return outcome if (result.rowcount or 0) == 1 else None


def clear_parse_run_lease(session: Session, run_id: uuid.UUID) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    row.lease_owner = None
    row.lease_until = None
    session.flush()


def patch_run_progress(
    session: Session,
    run_id: uuid.UUID,
    progress: dict,
    *,
    lease_owner: str | None = None,
) -> None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return
    now = datetime.now(UTC)
    row.progress = progress
    row.progress_updated_at = now
    # Lease продлевает только heartbeat с fencing; progress не трогает lease_until.
    if lease_owner and row.status == "running" and row.lease_owner == lease_owner.strip()[:64]:
        row.lease_until = _lease_deadline(now)
    session.flush()


def request_parse_run_cancel(session: Session, run_id: uuid.UUID) -> ParseRunRow | None:
    row = session.get(ParseRunRow, run_id)
    if row is None:
        return None
    if row.status in _TERMINAL_PARSE_STATUSES:
        return None
    row.cancel_requested = True
    if row.status == "queued":
        row.status = "cancelled"
        row.finished_at = datetime.now(UTC)
        row.lease_owner = None
        row.lease_until = None
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
    row.lease_owner = None
    row.lease_until = None
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
            # Lease-based stuck: без lease_until — fallback на started/created.
            heartbeat = func.coalesce(ParseRunRow.lease_until, ParseRunRow.started_at, ParseRunRow.created_at)
            stmt = stmt.where(heartbeat < older_than)
        else:
            stmt = stmt.where(ParseRunRow.created_at < older_than)
    return list(session.scalars(stmt))


def list_parse_runs_with_expired_lease(session: Session, *, now: datetime | None = None) -> list[ParseRunRow]:
    """Running с истёкшим lease (или без lease и старым started_at — через older_than в recover)."""
    ts = now or datetime.now(UTC)
    stmt = select(ParseRunRow).where(
        ParseRunRow.status == "running",
        ParseRunRow.lease_until.is_not(None),
        ParseRunRow.lease_until < ts,
    )
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
    lease_owner: str | None = None,
) -> bool:
    """Сменить статус. С ``lease_owner`` — только если lease ещё наш (fencing).

    Возвращает True, если запись обновлена.
    """
    now = datetime.now(UTC)
    if lease_owner and status in _TERMINAL_PARSE_STATUSES:
        owner = lease_owner.strip()[:64]
        values: dict = {
            "status": status,
            "finished_at": now,
            "lease_owner": None,
            "lease_until": None,
        }
        if error_message is not None:
            values["error_message"] = error_message
        result = session.execute(
            update(ParseRunRow)
            .where(
                ParseRunRow.id == run_id,
                ParseRunRow.status == "running",
                ParseRunRow.lease_owner == owner,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        return (result.rowcount or 0) == 1

    row = session.get(ParseRunRow, run_id)
    if row is None:
        return False
    row.status = status
    if status == "running" and row.started_at is None:
        row.started_at = now
    if status in _TERMINAL_PARSE_STATUSES:
        row.finished_at = now
        row.lease_owner = None
        row.lease_until = None
    if error_message is not None:
        row.error_message = error_message
    session.flush()
    return True


def finalize_parse_run(
    session: Session,
    run_id: uuid.UUID,
    result: ParseResult,
    *,
    status: str = "completed",
    lease_owner: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Сохранить метаданные прогона. С ``lease_owner`` — conditional UPDATE (fencing).

    Возвращает True, если запись обновлена. False = lease уже чужой / не running.
    """
    now = datetime.now(UTC)
    values = {
        "status": status,
        "finished_at": now,
        "lease_owner": None,
        "lease_until": None,
        "posts_fetched": result.posts_fetched,
        "parse_warnings": result.parse_warnings,
        "run_params": result.run_params,
        "metrics": result.metrics.model_dump(mode="json"),
    }
    if error_message is not None:
        values["error_message"] = error_message
    if lease_owner:
        owner = lease_owner.strip()[:64]
        exec_result = session.execute(
            update(ParseRunRow)
            .where(
                ParseRunRow.id == run_id,
                ParseRunRow.status == "running",
                ParseRunRow.lease_owner == owner,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        return (exec_result.rowcount or 0) == 1

    row = session.get(ParseRunRow, run_id)
    if row is None:
        return False
    row.status = status
    row.finished_at = now
    row.lease_owner = None
    row.lease_until = None
    row.posts_fetched = result.posts_fetched
    row.parse_warnings = result.parse_warnings
    row.run_params = result.run_params
    row.metrics = values["metrics"]
    if error_message is not None:
        row.error_message = error_message
    session.flush()
    return True


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
