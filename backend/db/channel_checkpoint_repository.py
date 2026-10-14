"""Чекпоинты инкрементальной вычитки Telegram-каналов (telegram_channel_checkpoints)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import TelegramChannelCheckpointRow
from tg_jobs_parser.parsers.base import ChannelCheckpoint


def load_channel_checkpoints(session: Session, channels: list[str]) -> dict[str, ChannelCheckpoint]:
    if not channels:
        return {}
    rows = session.scalars(
        select(TelegramChannelCheckpointRow).where(TelegramChannelCheckpointRow.channel.in_(channels))
    ).all()
    return {
        r.channel: ChannelCheckpoint(last_message_id=int(r.last_message_id), covered_from=r.covered_from) for r in rows
    }


def save_channel_checkpoints(
    session: Session,
    checkpoints: dict[str, ChannelCheckpoint],
    *,
    parse_run_id: uuid.UUID | None = None,
) -> None:
    """Upsert чекпоинтов. Вызывается в транзакции финализации ``completed``-прогона."""
    if not checkpoints:
        return
    now = datetime.now(UTC)
    existing = {
        r.channel: r
        for r in session.scalars(
            select(TelegramChannelCheckpointRow).where(TelegramChannelCheckpointRow.channel.in_(list(checkpoints)))
        ).all()
    }
    for channel, cp in checkpoints.items():
        row = existing.get(channel)
        if row is None:
            row = TelegramChannelCheckpointRow(channel=channel)
            session.add(row)
        row.last_message_id = cp.last_message_id
        row.covered_from = cp.covered_from
        row.parse_run_id = parse_run_id
        row.updated_at = now
    session.flush()
