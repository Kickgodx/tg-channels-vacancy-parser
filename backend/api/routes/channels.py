"""Кандидаты в источники по пересылкам — только admin (как и прогоны)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_db, require_admin, verify_api_key
from backend.db import channel_candidate_repository as candidates
from backend.schemas import ChannelCandidate, ChannelCandidatePatch, ChannelCandidatesResponse
from backend.settings import get_settings
from jobdesk.config.loader import load_config
from jobdesk.parsers.channel_username import normalize_channel_username

router = APIRouter(
    prefix="/channels",
    tags=["channels"],
    dependencies=[Depends(verify_api_key), Depends(require_admin)],
)


@router.get("/candidates", response_model=ChannelCandidatesResponse)
def list_channel_candidates(include_ignored: bool = False, db: Session = Depends(get_db)) -> ChannelCandidatesResponse:
    """Каналы, из которых пересылают в наши, кроме уже читаемых: больше пересланных вакансий — выше."""
    cfg = load_config(get_settings().config_path)
    configured = [normalize_channel_username(c) for c in cfg.sources.channels]
    known = candidates.known_channels(db, configured)
    rows = candidates.list_candidates(db, known=known, include_ignored=include_ignored)
    return ChannelCandidatesResponse(items=[ChannelCandidate(**r) for r in rows])


@router.patch("/candidates/{channel_id}", response_model=ChannelCandidatePatch)
def patch_channel_candidate(
    channel_id: int, body: ChannelCandidatePatch, db: Session = Depends(get_db)
) -> ChannelCandidatePatch:
    """Скрыть кандидата (``ignored``) или вернуть его в список (``new``)."""
    if not candidates.set_candidate_status(db, channel_id, body.status):
        raise HTTPException(status_code=404, detail="Channel candidate not found")
    return body
