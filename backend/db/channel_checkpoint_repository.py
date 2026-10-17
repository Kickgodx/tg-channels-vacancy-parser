"""Чекпоинты инкрементальной вычитки Telegram-каналов (telegram_channel_checkpoints)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import TelegramChannelCheckpointRow
from tg_jobs_parser.parsers.base import ChannelCheckpoint

CheckpointChanges = dict[str, tuple[ChannelCheckpoint | None, ChannelCheckpoint]]


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
) -> CheckpointChanges:
    """Upsert чекпоинтов. Вызывается в транзакции финализации ``completed``-прогона.

    Возвращает изменившиеся каналы: ``канал → (прежний чекпоинт или None, новый)``.
    """
    if not checkpoints:
        return {}
    now = datetime.now(UTC)
    changes: CheckpointChanges = {}
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
            changes[channel] = (None, cp)
        else:
            old = ChannelCheckpoint(last_message_id=int(row.last_message_id), covered_from=row.covered_from)
            if old != cp:
                changes[channel] = (old, cp)
        row.last_message_id = cp.last_message_id
        row.covered_from = cp.covered_from
        row.parse_run_id = parse_run_id
        row.updated_at = now
    session.flush()
    return changes


def format_checkpoint_changes(changes: CheckpointChanges) -> str:
    """``@chan 100→150 (с 2026-10-01)``; новый канал — ``@chan новый 150 (с …)``."""
    parts = []
    for channel, (old, new) in changes.items():
        if old is None:
            parts.append(f"@{channel} новый {new.last_message_id} (с {new.covered_from.isoformat()})")
            continue
        since = (
            new.covered_from.isoformat()
            if old.covered_from == new.covered_from
            else f"{old.covered_from.isoformat()}→{new.covered_from.isoformat()}"
        )
        parts.append(f"@{channel} {old.last_message_id}→{new.last_message_id} (с {since})")
    return ", ".join(parts)
