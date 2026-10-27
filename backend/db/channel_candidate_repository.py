"""Кандидаты в источники: пересылки из чужих каналов, агрегаты по ним, статус «скрыт»."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.models import ChannelCandidateRow, TelegramChannelCheckpointRow, TelegramForwardRow
from tg_jobs_parser.parsers.base import ForwardedPost

CANDIDATE_STATUSES = ("new", "ignored")


def save_forwards(session: Session, forwards: list[ForwardedPost]) -> int:
    """Записать пересылки прогона; вернуть, сколько постов пересылок было новыми.

    Username и название канала обновляются последними увиденными (пустые не затирают
    известные). Пост, уже записанный прошлым прогоном, становится вакансией, если хоть
    раз прошёл как кандидат.
    """
    if not forwards:
        return 0
    sources: dict[int, tuple[str | None, str | None]] = {}
    for f in forwards:
        prev = sources.get(f.source.channel_id, (None, None))
        sources[f.source.channel_id] = (f.source.username or prev[0], f.source.title or prev[1])
    cand = pg_insert(ChannelCandidateRow).values(
        [
            {"channel_id": cid, "username": u, "title": (t or "")[:256] or None, "updated_at": datetime.now(UTC)}
            for cid, (u, t) in sources.items()
        ]
    )
    c = ChannelCandidateRow.__table__.c
    session.execute(
        cand.on_conflict_do_update(
            index_elements=["channel_id"],
            set_={
                "username": func.coalesce(cand.excluded.username, c.username),
                "title": func.coalesce(cand.excluded.title, c.title),
                "updated_at": cand.excluded.updated_at,
            },
        )
    )
    rows = {
        (f.channel, f.post_id): {
            "channel": f.channel,
            "post_id": f.post_id,
            "source_channel_id": f.source.channel_id,
            "source_post_id": f.source.post_id,
            "post_date": f.post_date,
            "is_vacancy": f.is_vacancy,
        }
        for f in forwards
    }
    fw = pg_insert(TelegramForwardRow).values(list(rows.values()))
    t = TelegramForwardRow.__table__.c
    result = session.execute(
        fw.on_conflict_do_update(
            index_elements=["channel", "post_id"],
            set_={"is_vacancy": or_(t.is_vacancy, fw.excluded.is_vacancy)},
        ).returning(t.seen_at)
    )
    return len(result.all())


def known_channels(session: Session, configured: list[str]) -> set[str]:
    """Каналы, которые уже читаем: из ``config.yaml`` и с чекпоинтами прошлых прогонов (lower)."""
    known = {c.lower() for c in configured if c}
    known.update(str(ch).lower() for ch in session.scalars(select(TelegramChannelCheckpointRow.channel)))
    return known


def list_candidates(session: Session, *, known: set[str], include_ignored: bool, limit: int = 100) -> list[dict]:
    """Каналы с публичным username, которых нет среди наших: больше пересланных вакансий — выше."""
    f = TelegramForwardRow
    c = ChannelCandidateRow
    forwards = func.count()
    vacancies = func.count().filter(f.is_vacancy.is_(True))
    stmt = (
        select(
            c.channel_id,
            c.username,
            c.title,
            c.status,
            forwards,
            vacancies,
            func.array_agg(func.distinct(f.channel)),
            func.min(f.post_date),
            func.max(f.post_date),
            func.max(f.source_post_id),
        )
        .join(f, f.source_channel_id == c.channel_id)
        .where(c.username.is_not(None))
        .group_by(c.channel_id)
        .order_by(vacancies.desc(), forwards.desc(), c.channel_id)
    )
    if not include_ignored:
        stmt = stmt.where(c.status == "new")
    out: list[dict] = []
    for cid, username, title, status, n, nv, seen_in, first, last, src_post in session.execute(stmt):
        if username.lower() in known:
            continue
        out.append(
            {
                "channel_id": int(cid),
                "username": username,
                "title": title,
                "status": status,
                "forwards": int(n),
                "vacancy_forwards": int(nv),
                "seen_in": sorted(seen_in or []),
                "first_post_date": first,
                "last_post_date": last,
                "example_url": f"https://t.me/{username}/{src_post}" if src_post else None,
            }
        )
        if len(out) >= limit:
            break
    return out


def set_candidate_status(session: Session, channel_id: int, status: str) -> bool:
    row = session.get(ChannelCandidateRow, channel_id)
    if row is None:
        return False
    row.status = status
    row.updated_at = datetime.now(UTC)
    session.flush()
    return True
