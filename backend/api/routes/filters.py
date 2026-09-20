from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db import repository as repo
from backend.db.models import SavedFilterRow, UserRow
from backend.schemas import (
    SavedFilterCreate,
    SavedFilterListResponse,
    SavedFilterOut,
    VacancyQuery,
)

router = APIRouter(prefix="/filters", tags=["filters"])


def _to_out(row: SavedFilterRow) -> SavedFilterOut:
    return SavedFilterOut(
        id=row.id,
        name=row.name,
        params=VacancyQuery.model_validate(row.params or {}),
        created_at=row.created_at,
    )


@router.get("", response_model=SavedFilterListResponse)
def list_filters(
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> SavedFilterListResponse:
    items = [_to_out(r) for r in repo.list_saved_filters(db, user_id=user.id)]
    return SavedFilterListResponse(items=items)


@router.post("", response_model=SavedFilterOut, status_code=201)
def create_filter(
    body: SavedFilterCreate,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> SavedFilterOut:
    row = repo.create_saved_filter(
        db,
        name=body.name.strip(),
        params=body.params.model_dump(mode="json"),
        user_id=user.id,
    )
    return _to_out(row)


@router.delete("/{filter_id}", status_code=204)
def delete_filter(
    filter_id: int,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> None:
    if not repo.delete_saved_filter(db, filter_id, user_id=user.id):
        raise HTTPException(status_code=404, detail="Saved filter not found")
