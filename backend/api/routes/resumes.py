from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db import repository as repo
from backend.db.models import ResumeRow, UserRow
from backend.db.models import VacancyMatchRow
from backend.schemas import (
    MatchCreateResponse,
    MatchProgress,
    MatchStartRequest,
    ResumeCreate,
    ResumeListResponse,
    ResumeOut,
    VacancyMatchOut,
)
from backend.services import match_service
from backend.services.progress_bus import progress_bus
from backend.services.match_service import match_registry

resumes_router = APIRouter(prefix="/resumes", tags=["resumes"])
matches_router = APIRouter(prefix="/matches", tags=["matches"])


def _to_out(row) -> ResumeOut:
    return ResumeOut(id=row.id, name=row.name, text=row.text, created_at=row.created_at)


def _get_own_resume(db: Session, resume_id: int, user: UserRow) -> ResumeRow:
    row = repo.get_resume(db, resume_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Resume not found")
    return row


def _check_match_owner(match_id: uuid.UUID, user: UserRow):
    m = match_registry.get(match_id)
    if m is None or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="Match not found")
    return m


@resumes_router.get("", response_model=ResumeListResponse)
def list_resumes(
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> ResumeListResponse:
    items = [_to_out(r) for r in repo.list_resumes(db, user_id=user.id)]
    return ResumeListResponse(items=items)


@resumes_router.post("", response_model=ResumeOut, status_code=201)
def create_resume(
    body: ResumeCreate,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> ResumeOut:
    row = repo.create_resume(db, name=body.name.strip(), text=body.text, user_id=user.id)
    return _to_out(row)


@resumes_router.get("/{resume_id}", response_model=ResumeOut)
def get_resume(
    resume_id: int,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> ResumeOut:
    return _to_out(_get_own_resume(db, resume_id, user))


@resumes_router.delete("/{resume_id}", status_code=204)
def delete_resume(
    resume_id: int,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> None:
    if not repo.delete_resume(db, resume_id, user_id=user.id):
        raise HTTPException(status_code=404, detail="Resume not found")


def _match_to_out(row: VacancyMatchRow, *, cached: bool) -> VacancyMatchOut:
    return VacancyMatchOut(
        resume_id=row.resume_id,
        vacancy_id=row.vacancy_id,
        score=row.score,
        explanation=row.explanation,
        matching_skills=row.matching_skills or [],
        missing_skills=row.missing_skills or [],
        model=row.model,
        created_at=row.created_at,
        cached=cached,
    )


@resumes_router.get("/{resume_id}/match/{vacancy_id}", response_model=VacancyMatchOut)
def get_vacancy_match(
    resume_id: int,
    vacancy_id: str,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> VacancyMatchOut:
    """Уже посчитанная оценка пары; 404 — если матчинг ещё не запускался."""
    _get_own_resume(db, resume_id, user)
    row = repo.get_vacancy_match(db, resume_id=resume_id, vacancy_id=vacancy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return _match_to_out(row, cached=True)


@resumes_router.post("/{resume_id}/match/{vacancy_id}", response_model=VacancyMatchOut)
def match_single_vacancy(
    resume_id: int,
    vacancy_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> VacancyMatchOut:
    """Оценить одну вакансию синхронно. Без ``force`` возвращает сохранённую оценку."""
    resume = _get_own_resume(db, resume_id, user)
    vacancy = repo.get_vacancy_row(db, vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    if not force:
        existing = repo.get_vacancy_match(db, resume_id=resume_id, vacancy_id=vacancy_id)
        if existing is not None:
            return _match_to_out(existing, cached=True)
    try:
        row = match_service.match_single(
            db,
            resume_id=resume_id,
            resume_text=resume.text,
            vacancy_id=vacancy_id,
            vacancy_text=vacancy.cleaned_text or vacancy.raw_text or "",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM: {e}") from e
    return _match_to_out(row, cached=False)


@resumes_router.post("/{resume_id}/match", response_model=MatchCreateResponse, status_code=202)
def start_match(
    resume_id: int,
    body: MatchStartRequest,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> MatchCreateResponse:
    _get_own_resume(db, resume_id, user)
    try:
        match_id = match_service.start_match(
            resume_id, body.filters, limit=body.limit, user_id=user.id
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return MatchCreateResponse(match_id=match_id)


@matches_router.get("/{match_id}", response_model=MatchProgress)
def get_match(
    match_id: uuid.UUID,
    user: UserRow = Depends(get_current_user),
) -> MatchProgress:
    m = _check_match_owner(match_id, user)
    progress = match_service.get_status(m.match_id)
    assert progress is not None
    return progress


@matches_router.post("/{match_id}/cancel")
def cancel_match(
    match_id: uuid.UUID,
    user: UserRow = Depends(get_current_user),
) -> dict:
    m = _check_match_owner(match_id, user)
    if not match_service.cancel_match(m.match_id):
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
async def match_events(
    match_id: uuid.UUID,
    user: UserRow = Depends(get_current_user),
) -> StreamingResponse:
    _check_match_owner(match_id, user)
    return StreamingResponse(
        _sse_events(match_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
