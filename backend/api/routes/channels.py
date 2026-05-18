from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter

from tg_jobs_parser.channel_search import search_channels

from backend.api.deps import verify_api_key
from fastapi import Depends

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
