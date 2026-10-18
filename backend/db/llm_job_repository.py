"""Репозиторий фоновых LLM-джобов (match / cover letter / import)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from backend.db.models import LlmJobRow
from backend.settings import get_settings

_ACTIVE_LLM_STATUSES = ("queued", "running")
TERMINAL_LLM_STATUSES = frozenset({"done", "error", "cancelled"})

# Пространство ключей pg_advisory_xact_lock(int, int) постановки LLM-джоб; второй ключ — user_id.
_ENQUEUE_LOCK_NS = 874_291_003


def lock_user_llm_enqueue(session: Session, *, user_id: int) -> None:
    """Сериализовать постановку LLM-джоб одного пользователя до конца транзакции.

    Без этого параллельные запросы проходят «нет активной» и «лимит не исчерпан»
    одновременно: дубль джобы и обход ``interactive_per_user_limit``. Дубль пары
    дополнительно запрещает ``uq_llm_jobs_one_active_pair``, лимит — только этот lock.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), CAST(:uid AS integer))"),
        {"ns": _ENQUEUE_LOCK_NS, "uid": user_id},
    )


def create_llm_job(
    session: Session,
    *,
    user_id: int,
    kind: str,
    resume_id: int | None = None,
    vacancy_id: str | None = None,
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


def _lease_deadline(now: datetime | None = None) -> datetime:
    base = now or datetime.now(UTC)
    seconds = max(30, get_settings().llm_job_lease_seconds)
    return base + timedelta(seconds=seconds)


def new_llm_lease_owner() -> str:
    return f"llm-{uuid.uuid4().hex}"[:64]


def claim_llm_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    lease_owner: str | None = None,
) -> LlmJobRow | None:
    """Атомарно ``queued`` → ``running``. Только победитель получает строку."""
    now = datetime.now(UTC)
    owner = (lease_owner or new_llm_lease_owner()).strip()[:64] or new_llm_lease_owner()
    result = session.execute(
        update(LlmJobRow)
        .where(LlmJobRow.id == job_id, LlmJobRow.status == "queued")
        .values(
            status="running",
            started_at=now,
            lease_owner=owner,
            lease_until=_lease_deadline(now),
            attempts=LlmJobRow.attempts + 1,
        )
    )
    if (result.rowcount or 0) != 1:
        return None
    session.flush()
    return session.get(LlmJobRow, job_id)


def touch_llm_job_lease(
    session: Session,
    job_id: uuid.UUID,
    *,
    lease_owner: str,
) -> bool:
    now = datetime.now(UTC)
    owner = lease_owner.strip()[:64]
    result = session.execute(
        update(LlmJobRow)
        .where(
            LlmJobRow.id == job_id,
            LlmJobRow.status == "running",
            LlmJobRow.lease_owner == owner,
        )
        .values(lease_until=_lease_deadline(now))
    )
    session.flush()
    return (result.rowcount or 0) == 1


def patch_llm_job_result(
    session: Session,
    job_id: uuid.UUID,
    result: dict,
    *,
    lease_owner: str,
) -> bool:
    """Промежуточный итог выполняемой джобы; заодно продлевает lease. False — lease уже не наш."""
    now = datetime.now(UTC)
    updated = session.execute(
        update(LlmJobRow)
        .where(
            LlmJobRow.id == job_id,
            LlmJobRow.status == "running",
            LlmJobRow.lease_owner == lease_owner.strip()[:64],
        )
        .values(result=result, lease_until=_lease_deadline(now))
        .execution_options(synchronize_session=False)
    )
    session.flush()
    return (updated.rowcount or 0) == 1


def attempts_exhausted_message(attempts: int) -> str:
    return f"Превышено число попыток ({attempts}): воркер не довёл задачу до конца (падение процесса или потеря lease)."


def _reclaim_running_llm_job(session: Session, job_id: uuid.UUID, ts: datetime, *lease_conditions) -> str | None:
    """Зависший ``running`` → ``cancelled`` (отмена запрошена), ``error`` (лимит попыток
    исчерпан) либо ``queued``.

    Условные UPDATE вместо чтения строки: исход определяется атомарно.
    """
    max_attempts = max(1, get_settings().llm_job_max_attempts)
    stuck = (LlmJobRow.id == job_id, LlmJobRow.status == "running", *lease_conditions)
    released = {"rq_job_id": None, "lease_owner": None, "lease_until": None}

    cancelled = session.execute(
        update(LlmJobRow)
        .where(*stuck, LlmJobRow.cancel_requested.is_(True))
        .values(status="cancelled", finished_at=ts, **released)
        .execution_options(synchronize_session=False)
    )
    if (cancelled.rowcount or 0) == 1:
        session.expire_all()
        return "cancelled"

    exhausted = session.execute(
        update(LlmJobRow)
        .where(*stuck, LlmJobRow.attempts >= max_attempts)
        .values(
            status="error",
            finished_at=ts,
            error_message=attempts_exhausted_message(max_attempts),
            **released,
        )
        .execution_options(synchronize_session=False)
    )
    if (exhausted.rowcount or 0) == 1:
        session.expire_all()
        return "error"

    requeued = session.execute(
        update(LlmJobRow)
        .where(*stuck, LlmJobRow.attempts < max_attempts)
        .values(status="queued", started_at=None, finished_at=None, error_message=None, **released)
        .execution_options(synchronize_session=False)
    )
    session.expire_all()
    return "queued" if (requeued.rowcount or 0) == 1 else None


def reclaim_expired_llm_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> str | None:
    """Атомарно running(expired lease) → queued|error|cancelled. None — перехватить не удалось.

    ``error`` — исчерпан лимит попыток (``LLM_JOB_MAX_ATTEMPTS``), ``cancelled`` — отмена
    запрошена до того, как воркер пропал.
    """
    ts = now or datetime.now(UTC)
    return _reclaim_running_llm_job(
        session,
        job_id,
        ts,
        LlmJobRow.lease_until.is_not(None),
        LlmJobRow.lease_until < ts,
    )


def reclaim_legacy_stale_llm_job(session: Session, job_id: uuid.UUID) -> str | None:
    """То же для running без ``lease_until`` (legacy); возраст проверяет вызывающий."""
    return _reclaim_running_llm_job(session, job_id, datetime.now(UTC), LlmJobRow.lease_until.is_(None))


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


def request_llm_job_cancel(session: Session, job_id: uuid.UUID) -> LlmJobRow | None:
    """Запросить отмену: ``queued`` → сразу ``cancelled``, ``running`` → флаг для воркера.

    Завершённая задача не меняется. Перевод из ``queued`` — условным UPDATE: если
    воркер успел взять задачу, остаётся флаг, и задачу останавливает он. None — нет задачи.
    """
    row = session.get(LlmJobRow, job_id)
    if row is None:
        return None
    if row.status in TERMINAL_LLM_STATUSES:
        return row
    now = datetime.now(UTC)
    cancelled = session.execute(
        update(LlmJobRow)
        .where(LlmJobRow.id == job_id, LlmJobRow.status == "queued")
        .values(
            status="cancelled",
            cancel_requested=True,
            finished_at=now,
            lease_owner=None,
            lease_until=None,
        )
        .execution_options(synchronize_session=False)
    )
    if (cancelled.rowcount or 0) != 1:
        session.execute(
            update(LlmJobRow)
            .where(LlmJobRow.id == job_id, LlmJobRow.status == "running")
            .values(cancel_requested=True)
            .execution_options(synchronize_session=False)
        )
    session.flush()
    session.expire_all()
    return session.get(LlmJobRow, job_id)


def is_llm_job_cancel_requested(session: Session, job_id: uuid.UUID) -> bool:
    return bool(session.scalar(select(LlmJobRow.cancel_requested).where(LlmJobRow.id == job_id)))


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
    lease_owner: str | None = None,
) -> LlmJobRow | None:
    """Сменить статус. С ``lease_owner`` на terminal — только если lease ещё наш."""
    now = datetime.now(UTC)
    if lease_owner and status in TERMINAL_LLM_STATUSES:
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
            update(LlmJobRow)
            .where(
                LlmJobRow.id == job_id,
                LlmJobRow.status == "running",
                LlmJobRow.lease_owner == owner,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        if (result.rowcount or 0) != 1:
            return None
        return session.get(LlmJobRow, job_id)

    row = session.get(LlmJobRow, job_id)
    if row is None:
        return None
    row.status = status
    if status == "running" and row.started_at is None:
        row.started_at = now
        row.lease_until = _lease_deadline(now)
        if row.lease_owner is None:
            row.lease_owner = "worker"
    if status in TERMINAL_LLM_STATUSES:
        row.finished_at = now
        row.lease_owner = None
        row.lease_until = None
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
            heartbeat = func.coalesce(LlmJobRow.lease_until, LlmJobRow.started_at, LlmJobRow.created_at)
            stmt = stmt.where(heartbeat < older_than)
        else:
            stmt = stmt.where(LlmJobRow.created_at < older_than)
    return list(session.scalars(stmt).all())


def list_llm_jobs_with_expired_lease(session: Session, *, now: datetime | None = None) -> list[LlmJobRow]:
    ts = now or datetime.now(UTC)
    stmt = select(LlmJobRow).where(
        LlmJobRow.status == "running",
        LlmJobRow.lease_until.is_not(None),
        LlmJobRow.lease_until < ts,
    )
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
    row.lease_owner = None
    row.lease_until = None
    session.flush()
    return row


def count_llm_jobs_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(select(LlmJobRow.status, func.count()).group_by(LlmJobRow.status))
    return {str(status): int(n) for status, n in rows}


def purge_finished_llm_jobs(session: Session, *, older_than: datetime, limit: int = 1000) -> int:
    """Удалить до ``limit`` завершённых (done/error/cancelled) джоб, закончившихся раньше ``older_than``.

    Результат джобы хранится отдельно (``vacancy_matches`` / ``cover_letters``), строка
    джобы после завершения нужна только SSE/опросу статуса.
    """
    finished = (
        select(LlmJobRow.id)
        .where(
            LlmJobRow.status.in_(TERMINAL_LLM_STATUSES),
            func.coalesce(LlmJobRow.finished_at, LlmJobRow.created_at) < older_than,
        )
        .limit(limit)
    )
    result = session.execute(delete(LlmJobRow).where(LlmJobRow.id.in_(finished)))
    return int(result.rowcount or 0)
