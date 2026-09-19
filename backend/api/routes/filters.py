from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.db import repository as repo
from backend.db.models import SavedFilterRow
from backend.schemas import (
    SavedFilterCreate,
    SavedFilterListResponse,
    SavedFilterOut,
    VacancyQuery,
)

router = APIRouter(prefix="/filters", tags=["filters"], dependencies=[Depends(verify_api_key)])


def _to_out(row: SavedFilterRow) -> SavedFilterOut:
    return SavedFilterOut(
        id=row.id,
        name=row.name,
        params=VacancyQuery.model_validate(row.params or {}),
        created_at=row.created_at,
    )


@router.get("", response_model=SavedFilterListResponse)
def list_filters(db: Session = Depends(get_db)) -> SavedFilterListResponse:
    return SavedFilterListResponse(items=[_to_out(r) for r in repo.list_saved_filters(db)])


@router.post("", response_model=SavedFilterOut, status_code=201)
def create_filter(body: SavedFilterCreate, db: Session = Depends(get_db)) -> SavedFilterOut:
    row = repo.create_saved_filter(
        db, name=body.name.strip(), params=body.params.model_dump(mode="json")
    )
    return _to_out(row)


@router.delete("/{filter_id}", status_code=204)
def delete_filter(filter_id: int, db: Session = Depends(get_db)) -> None:
    if not repo.delete_saved_filter(db, filter_id):
        raise HTTPException(status_code=404, detail="Saved filter not found")
