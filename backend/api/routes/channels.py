from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.deps import verify_api_key
from tg_jobs_parser.channel_search import search_channels

router = APIRouter(prefix="/channels", tags=["channels"], dependencies=[Depends(verify_api_key)])


class ChannelSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=15, ge=1, le=50)


class ChannelSearchResponse(BaseModel):
    found: list[str]
    backend: str
    query: str


@router.post("/search", response_model=ChannelSearchResponse)
def search(body: ChannelSearchRequest) -> ChannelSearchResponse:
    found, backend = search_channels(body.query, limit=body.limit)
    return ChannelSearchResponse(found=found, backend=backend, query=body.query.strip())
