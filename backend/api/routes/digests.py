"""Еженедельный обзор рынка: чтение — любому залогиненному, формирование — админу."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import CurrentUser, get_current_user, get_db, require_admin, verify_api_key
from backend.errors import CODE_VALIDATION, http_error
from backend.schemas import DigestGenerateRequest, DigestListResponse, DigestOut
from backend.services import market_digest_service as digests

router = APIRouter(
    prefix="/digests",
    tags=["digests"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)


@router.get("", response_model=DigestListResponse)
def list_digests(db: Session = Depends(get_db)) -> DigestListResponse:
    """Обзоры за последние 52 недели, свежие первыми."""
    return DigestListResponse(items=digests.list_digests(db))


@router.get("/{period_start}", response_model=DigestOut)
def get_digest(period_start: date, db: Session = Depends(get_db)) -> DigestOut:
    """Обзор недели, начинающейся с ``period_start`` (понедельник); нет — 404."""
    row = digests.get_digest(db, period_start)
    if row is None:
        raise HTTPException(status_code=404, detail="Digest not found")
    return digests.to_out(row)


@router.post("", response_model=DigestOut)
def generate_digest(
    body: DigestGenerateRequest | None = None,
    _admin: CurrentUser = Depends(require_admin),
) -> DigestOut:
    """Сформировать обзор недели (по умолчанию прошедшей). Есть и без ``force`` — отдаётся как есть.

    Вызов LLM идёт в запросе (один, без удержания соединения с БД): это ручное действие админа.
    """
    body = body or DigestGenerateRequest()
    today = datetime.now(UTC).date()
    week_start = body.week_start or digests.last_complete_week_start(today)
    try:
        digests.check_week_start(week_start, today=today)
    except digests.DigestWeekError as e:
        raise http_error(422, CODE_VALIDATION, str(e), retryable=False) from e
    return digests.generate_digest(week_start, force=body.force)
