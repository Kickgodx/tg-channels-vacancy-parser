"""Запросы фоновой работы вне расписания (``maintenance_requests``): одна строка на задачу."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import MaintenanceRequestRow

TASK_MARKET_MAP = "market_map"


def request_task(session: Session, task: str) -> datetime:
    """Запросить задачу; повторный запрос обновляет время. Возвращает время запроса."""
    now = datetime.now(UTC)
    stmt = pg_insert(MaintenanceRequestRow).values(task=task, requested_at=now)
    session.execute(stmt.on_conflict_do_update(index_elements=["task"], set_={"requested_at": now}))
    return now


def pending_since(session: Session, task: str) -> datetime | None:
    """Время невыполненного запроса задачи; None — запроса нет."""
    return session.scalar(select(MaintenanceRequestRow.requested_at).where(MaintenanceRequestRow.task == task))


def complete_task(session: Session, task: str, *, requested_at: datetime) -> None:
    """Снять запрос, выполненный исполнителем. Запрос, пришедший во время работы, остаётся."""
    session.execute(
        delete(MaintenanceRequestRow).where(
            MaintenanceRequestRow.task == task, MaintenanceRequestRow.requested_at <= requested_at
        )
    )
