from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.db import repository as repo
from backend.schemas import (
    MatchCreateResponse,
    MatchProgress,
    MatchStartRequest,
    ResumeCreate,
    ResumeListResponse,
    ResumeOut,
)
from backend.services import match_service
from backend.services.progress_bus import progress_bus
from backend.services.match_service import match_registry

resumes_router = APIRouter(
    prefix="/resumes", tags=["resumes"], dependencies=[Depends(verify_api_key)]
)
matches_router = APIRouter(
    prefix="/matches", tags=["matches"], dependencies=[Depends(verify_api_key)]
)


def _to_out(row) -> ResumeOut:
    return ResumeOut(id=row.id, name=row.name, text=row.text, created_at=row.created_at)


@resumes_router.get("", response_model=ResumeListResponse)
def list_resumes(db: Session = Depends(get_db)) -> ResumeListResponse:
    return ResumeListResponse(items=[_to_out(r) for r in repo.list_resumes(db)])


@resumes_router.post("", response_model=ResumeOut, status_code=201)
def create_resume(body: ResumeCreate, db: Session = Depends(get_db)) -> ResumeOut:
    row = repo.create_resume(db, name=body.name.strip(), text=body.text)
    return _to_out(row)


@resumes_router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeOut:
    row = repo.get_resume(db, resume_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return _to_out(row)


@resumes_router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: int, db: Session = Depends(get_db)) -> None:
    if not repo.delete_resume(db, resume_id):
        raise HTTPException(status_code=404, detail="Resume not found")


@resumes_router.post("/{resume_id}/match", response_model=MatchCreateResponse, status_code=202)
def start_match(
    resume_id: int,
    body: MatchStartRequest,
    db: Session = Depends(get_db),
) -> MatchCreateResponse:
    if repo.get_resume(db, resume_id) is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    try:
        match_id = match_service.start_match(
            resume_id, body.filters, limit=body.limit
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return MatchCreateResponse(match_id=match_id)


@matches_router.get("/{match_id}", response_model=MatchProgress)
def get_match(match_id: uuid.UUID) -> MatchProgress:
    status = match_service.get_status(match_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return status


@matches_router.post("/{match_id}/cancel")
def cancel_match(match_id: uuid.UUID) -> dict:
    if not match_service.cancel_match(match_id):
        raise HTTPException(status_code=404, detail="Match not running or not found")
    return {"ok": True, "match_id": str(match_id)}


async def _sse_events(match_id: uuid.UUID) -> AsyncIterator[str]:
    q = await progress_bus.subscribe(match_id)
    try:
        while True:
            snap = await q.get()
            if snap is None:
                yield "event: done\ndata: {}\n\n"
                break
            if isinstance(snap, MatchProgress):
                yield f"event: progress\ndata: {snap.model_dump_json()}\n\n"
    finally:
        progress_bus.unsubscribe(match_id, q)


@matches_router.get("/{match_id}/events")
async def match_events(match_id: uuid.UUID) -> StreamingResponse:
    if match_registry.get(match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return StreamingResponse(
        _sse_events(match_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
