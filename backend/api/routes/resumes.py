from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db import repository as repo
from backend.db.models import ResumeRow, UserRow, VacancyMatchRow
from backend.observability import record_match
from backend.schemas import (
    ResumeCreate,
    ResumeListResponse,
    ResumeOut,
    ResumeUpdate,
    VacancyMatchOut,
)
from backend.services import match_service
from tg_jobs_parser.ai.schema import MatchBreakdown, SkillMatch

_LOG = logging.getLogger(__name__)

resumes_router = APIRouter(prefix="/resumes", tags=["resumes"])


def _to_out(row) -> ResumeOut:
    return ResumeOut(id=row.id, name=row.name, text=row.text, created_at=row.created_at)


def _get_own_resume(db: Session, resume_id: int, user: UserRow) -> ResumeRow:
    row = repo.get_resume(db, resume_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Resume not found")
    return row


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


@resumes_router.put("/{resume_id}", response_model=ResumeOut)
def update_resume(
    resume_id: int,
    body: ResumeUpdate,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> ResumeOut:
    name = body.name.strip() if body.name is not None else None
    row = repo.update_resume(db, resume_id, user_id=user.id, name=name, text=body.text)
    if row is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return _to_out(row)


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
        overall_score=row.overall_score,
        confidence=row.confidence,
        breakdown=MatchBreakdown.model_validate(row.breakdown or {}),
        explanation=row.explanation,
        matching_skills=[
            SkillMatch.model_validate(s) for s in row.matching_skills or []
        ],
        missing_skills=[
            SkillMatch.model_validate(s) for s in row.missing_skills or []
        ],
        strengths=row.strengths or [],
        risks=row.risks or [],
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
            record_match(outcome="cached")
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
        _LOG.warning(
            "Матчинг резюме %s ↔ вакансия %s не удался: %s", resume_id, vacancy_id, e
        )
        raise HTTPException(status_code=502, detail=f"LLM: {e}") from e
    return _match_to_out(row, cached=False)
