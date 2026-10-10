"""Репозиторий фоновых LLM-джобов (match / cover letter)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.db.models import LlmJobRow

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
