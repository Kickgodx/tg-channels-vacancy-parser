"""Суточная квота вызовов LLM на пользователя (llm_user_usage)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.db.models import LlmUserUsageRow


def _today() -> date:
    return datetime.now(UTC).date()


def try_reserve_llm_calls(
    session: Session,
    *,
    user_id: int,
    calls: int,
    daily_limit: int,
    day: date | None = None,
) -> bool:
    """Атомарно списать ``calls`` из суточной квоты. False — квоты не хватает, ничего не списано.

    Один INSERT … ON CONFLICT DO UPDATE … WHERE: параллельные запросы не
    проскочат лимит между проверкой и записью.
    """
    if calls <= 0:
        return True
    if calls > daily_limit:
        return False
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = (
        insert(LlmUserUsageRow)
        .values(user_id=user_id, day=day or _today(), calls=calls)
        .on_conflict_do_update(
            index_elements=[LlmUserUsageRow.user_id, LlmUserUsageRow.day],
            set_={"calls": LlmUserUsageRow.calls + calls},
            where=LlmUserUsageRow.calls + calls <= daily_limit,
        )
        .returning(LlmUserUsageRow.calls)
    )
    return session.execute(stmt).first() is not None


def llm_calls_today(session: Session, *, user_id: int, day: date | None = None) -> int:
    used = session.scalar(
        select(LlmUserUsageRow.calls).where(
            LlmUserUsageRow.user_id == user_id, LlmUserUsageRow.day == (day or _today())
        )
    )
    return int(used or 0)
