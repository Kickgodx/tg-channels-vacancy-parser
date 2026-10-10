"""Статус фоновых LLM-джоб (мэтчинг / сопроводительные письма)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.api.routes.resumes import _letter_to_out, _match_to_out
from backend.db import repository as repo
from backend.db.models import UserRow
from backend.schemas import LlmJobOut
from backend.services.llm_job_service import KIND_COVER_LETTER, KIND_MATCH

router = APIRouter(prefix="/llm-jobs", tags=["llm-jobs"])


@router.get("/{job_id}", response_model=LlmJobOut)
def get_llm_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> LlmJobOut:
    row = repo.get_llm_job(db, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    match_out = None
    letter_out = None
    if row.status == "done":
        if row.kind == KIND_MATCH:
            m = repo.get_vacancy_match(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if m is not None:
                match_out = _match_to_out(m, cached=False)
        elif row.kind == KIND_COVER_LETTER:
            c = repo.get_cover_letter(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if c is not None:
                letter_out = _letter_to_out(c, cached=False)

    return LlmJobOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        resume_id=row.resume_id,
        vacancy_id=row.vacancy_id,
        error_message=row.error_message,
        match=match_out,
        cover_letter=letter_out,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )
