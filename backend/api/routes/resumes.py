from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db import repository as repo
from backend.db.models import CoverLetterRow, ResumeRow, UserRow, VacancyMatchRow
from backend.observability import record_cover_letter, record_match
from backend.schemas import (
    CoverLetterOut,
    CoverLetterRequest,
    LlmJobAccepted,
    ResumeCreate,
    ResumeListResponse,
    ResumeOut,
    ResumeUpdate,
    VacancyMatchOut,
)
from backend.services.llm_job_service import (
    KIND_COVER_LETTER,
    KIND_MATCH,
    LlmDailyQuotaError,
    LlmJobUserLimitError,
    llm_job_runner,
)
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
        matching_skills=[SkillMatch.model_validate(s) for s in row.matching_skills or []],
        missing_skills=[SkillMatch.model_validate(s) for s in row.missing_skills or []],
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


@resumes_router.post(
    "/{resume_id}/match/{vacancy_id}",
    response_model=None,
    responses={
        200: {"model": VacancyMatchOut},
        202: {"model": LlmJobAccepted},
    },
)
def match_single_vacancy(
    resume_id: int,
    vacancy_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
):
    """Кэш без force → 200; иначе фоновая джоба → 202 ``{job_id}``."""
    _get_own_resume(db, resume_id, user)
    vacancy = repo.get_vacancy_row(db, vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    if not force:
        existing = repo.get_vacancy_match(db, resume_id=resume_id, vacancy_id=vacancy_id)
        if existing is not None:
            record_match(outcome="cached")
            return _match_to_out(existing, cached=True)
    try:
        result = llm_job_runner.enqueue(
            user_id=user.id,
            kind=KIND_MATCH,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            force=force,
        )
    except LlmJobUserLimitError as e:
        from backend.errors import CODE_RATE_LIMITED, http_error

        raise http_error(429, CODE_RATE_LIMITED, str(e), retryable=True) from e
    except LlmDailyQuotaError as e:
        from backend.errors import CODE_LLM_QUOTA_EXCEEDED, http_error

        raise http_error(429, CODE_LLM_QUOTA_EXCEEDED, str(e), retryable=False) from e
    body = LlmJobAccepted(job_id=result.job_id, status="queued")
    return JSONResponse(status_code=202, content=body.model_dump(mode="json"))


def _letter_to_out(row: CoverLetterRow, *, cached: bool) -> CoverLetterOut:
    return CoverLetterOut(
        resume_id=row.resume_id,
        vacancy_id=row.vacancy_id,
        text=row.text,
        tone=row.tone,
        model=row.model,
        created_at=row.created_at,
        cached=cached,
    )


@resumes_router.get("/{resume_id}/cover-letter/{vacancy_id}", response_model=CoverLetterOut)
def get_cover_letter(
    resume_id: int,
    vacancy_id: str,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> CoverLetterOut:
    """Сохранённое письмо пары; 404 — если генерация ещё не запускалась."""
    _get_own_resume(db, resume_id, user)
    row = repo.get_cover_letter(db, resume_id=resume_id, vacancy_id=vacancy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Cover letter not found")
    return _letter_to_out(row, cached=True)


@resumes_router.post(
    "/{resume_id}/cover-letter/{vacancy_id}",
    response_model=None,
    responses={
        200: {"model": CoverLetterOut},
        202: {"model": LlmJobAccepted},
    },
)
def generate_cover_letter(
    resume_id: int,
    vacancy_id: str,
    body: CoverLetterRequest | None = None,
    force: bool = False,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
):
    """Кэш без force → 200; иначе фоновая джоба → 202 ``{job_id}``."""
    _get_own_resume(db, resume_id, user)
    vacancy = repo.get_vacancy_row(db, vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    if not force:
        existing = repo.get_cover_letter(db, resume_id=resume_id, vacancy_id=vacancy_id)
        if existing is not None:
            record_cover_letter(outcome="cached")
            return _letter_to_out(existing, cached=True)
    tone = body.tone if body else "neutral"
    extra_notes = body.extra_notes if body else None
    try:
        result = llm_job_runner.enqueue(
            user_id=user.id,
            kind=KIND_COVER_LETTER,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            force=force,
            params={"tone": tone, "extra_notes": extra_notes},
        )
    except LlmJobUserLimitError as e:
        from backend.errors import CODE_RATE_LIMITED, http_error

        raise http_error(429, CODE_RATE_LIMITED, str(e), retryable=True) from e
    except LlmDailyQuotaError as e:
        from backend.errors import CODE_LLM_QUOTA_EXCEEDED, http_error

        raise http_error(429, CODE_LLM_QUOTA_EXCEEDED, str(e), retryable=False) from e
    accepted = LlmJobAccepted(job_id=result.job_id, status="queued")
    return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))


@resumes_router.delete("/{resume_id}/cover-letter/{vacancy_id}", status_code=204)
def delete_cover_letter(
    resume_id: int,
    vacancy_id: str,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> None:
    """Сбросить сохранённое письмо пары."""
    _get_own_resume(db, resume_id, user)
    if not repo.delete_cover_letter(db, resume_id=resume_id, vacancy_id=vacancy_id):
        raise HTTPException(status_code=404, detail="Cover letter not found")
