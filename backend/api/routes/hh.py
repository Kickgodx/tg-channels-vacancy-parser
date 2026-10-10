"""Admin-only HH.ru sync endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.api.deps import require_admin, verify_api_key
from backend.db import hh_repository as hh_repo
from backend.db.models import UserRow
from backend.db.session import get_db
from backend.services import hh_sync_service
from backend.services.hh_sync_service import HhSyncConflict, HhSyncNotConfigured
from backend.settings import Settings, get_settings
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.hh.client import HHApiError

_LOG = logging.getLogger(__name__)

router = APIRouter(
    prefix="/hh",
    tags=["hh"],
    dependencies=[Depends(verify_api_key)],
)


class HhSyncResponse(BaseModel):
    searched: int
    listings_upserted: int
    unique: int
    duplicates: int
    details_fetched: int
    details_skipped: int
    vacancies_inserted: int
    vacancies_updated: int
    listings_purged: int = 0
    pages: int = 0
    flush_batches: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float


class HhStatusResponse(BaseModel):
    enabled: bool
    token_configured: bool
    email_configured: bool
    listing_ttl_days: int
    search_text: str
    professional_role: list[str]
    listings_total: int
    listings_with_detail: int
    listings_without_detail: int


@router.get("/status", response_model=HhStatusResponse)
def hh_status(
    db: Session = Depends(get_db),
    _admin: UserRow = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> HhStatusResponse:
    cfg = load_config(settings.config_path)
    counts = hh_repo.listing_counts(db)
    return HhStatusResponse(
        enabled=cfg.hh.enabled,
        token_configured=bool((settings.hh_api_token or "").strip()),
        email_configured=bool((settings.hh_contact_email or "").strip()),
        listing_ttl_days=cfg.hh.listing_ttl_days,
        search_text=cfg.hh.search.text,
        professional_role=list(cfg.hh.search.professional_role),
        listings_total=counts.total,
        listings_with_detail=counts.with_detail,
        listings_without_detail=counts.without_detail,
    )


@router.post("/sync", response_model=HhSyncResponse)
def hh_sync(
    db: Session = Depends(get_db),
    _admin: UserRow = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> HhSyncResponse:
    cfg = load_config(settings.config_path)
    try:
        hh_sync_service.ensure_hh_ready(settings, cfg.hh)
    except HhSyncNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        result = hh_sync_service.run_hh_sync(db, config=cfg, settings=settings)
    except HhSyncConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HhSyncNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HHApiError as exc:
        _LOG.exception("HH sync API error")
        raise HTTPException(
            status_code=502,
            detail=f"HH API error: HTTP {exc.status_code}",
        ) from exc
    except Exception as exc:
        _LOG.exception("HH sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return HhSyncResponse(
        searched=result.searched,
        listings_upserted=result.listings_upserted,
        unique=result.unique,
        duplicates=result.duplicates,
        details_fetched=result.details_fetched,
        details_skipped=result.details_skipped,
        vacancies_inserted=result.vacancies_inserted,
        vacancies_updated=result.vacancies_updated,
        listings_purged=result.listings_purged,
        pages=result.pages,
        flush_batches=result.flush_batches,
        errors=result.errors,
        duration_seconds=result.duration_seconds,
    )
