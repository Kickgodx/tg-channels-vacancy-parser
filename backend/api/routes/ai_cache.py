from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.db.ai_cache_repository import cache_clear_all, cache_count

router = APIRouter(prefix="/ai-cache", tags=["ai-cache"], dependencies=[Depends(verify_api_key)])


class AICacheClearResponse(BaseModel):
    deleted: int
    remaining: int


class AICacheStatsResponse(BaseModel):
    count: int


@router.get("/stats")
def ai_cache_stats(db: Session = Depends(get_db)) -> AICacheStatsResponse:
    return AICacheStatsResponse(count=cache_count(db))


@router.post("/clear")
def clear_ai_cache(db: Session = Depends(get_db)) -> AICacheClearResponse:
    deleted = cache_clear_all(db)
    return AICacheClearResponse(deleted=deleted, remaining=cache_count(db))
