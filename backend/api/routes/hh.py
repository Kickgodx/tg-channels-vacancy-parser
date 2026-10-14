"""Admin-only HH.ru sync endpoints.

Sync — фоновая durable-джоба (``hh_sync_jobs``): ``POST /hh/sync`` отвечает 202 с
``job_id``, статус и итог — ``GET /hh/sync-jobs/{id}`` или SSE ``.../events``.
Внутри HTTP-запроса sync шёл 11+ минут (до ~2000 detail-запросов при 3 rps): поток,
сессия и соединение БД держались всё это время, а reverse-proxy обрывал запрос раньше.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.api.deps import (
    _extract_token,
    _user_from_token,
    bearer_token,
    require_admin,
    verify_api_key,
    verify_api_key_sse,
)
from backend.api.sse import (
    DB_POLL_INTERVAL_S,
    HEARTBEAT_INTERVAL_S,
    IDLE_DB_CHECK_S,
    PUBSUB_WAIT_S,
    SSE_DONE,
    SSE_HEADERS,
    SSE_PING,
    Ticker,
    sse_event,
)
from backend.db import hh_repository as hh_repo
from backend.db import hh_sync_job_repository as hh_jobs
from backend.db.models import HhSyncJobRow, UserRow
from backend.db.session import get_db, session_scope
from backend.errors import (
    CODE_FORBIDDEN,
    CODE_IDEMPOTENCY_IN_PROGRESS,
    CODE_NOT_AUTHENTICATED,
    CODE_UPSTREAM_UNAVAILABLE,
    CODE_VALIDATION,
    AppError,
    http_error,
)
from backend.services import hh_sync_job_service, hh_sync_service, idempotency
from backend.services.hh_sync_events import channel_for
from backend.services.hh_sync_service import HhSyncNotConfigured
from backend.services.hh_sync_window import HhSyncWindowError, resolve_hh_search_window
from backend.services.pubsub_hub import PubSubUnavailable, get_pubsub_hub
from backend.settings import Settings, get_settings
from tg_jobs_parser.config.loader import load_config

_LOG = logging.getLogger(__name__)

router = APIRouter(
    prefix="/hh",
    tags=["hh"],
    dependencies=[Depends(verify_api_key)],
)
# EventSource не шлёт заголовки: API key и JWT — query-параметрами.
sse_router = APIRouter(prefix="/hh", tags=["hh"])

HhSyncJobStatus = Literal["queued", "running", "completed", "failed"]


class HhSyncRequest(BaseModel):
    window: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class HhSyncResponse(BaseModel):
    """Итог sync (``HhSyncResult``); у упавшего sync — частичный, на момент сбоя."""

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
    window: str = "all"
    period: int | None = None
    date_from: str | None = None
    date_to: str | None = None


class HhSyncProgress(BaseModel):
    phase: str  # search | details | purge
    done: int = 0
    total: int = 0
    searched: int = 0
    pages: int = 0
    unique: int = 0
    duplicates: int = 0
    details_fetched: int = 0
    details_skipped: int = 0
    vacancies_inserted: int = 0
    vacancies_updated: int = 0
    errors: int = 0


class HhSyncAccepted(BaseModel):
    job_id: uuid.UUID
    status: HhSyncJobStatus


class HhSyncJobOut(BaseModel):
    id: uuid.UUID
    status: HhSyncJobStatus
    window: str = "all"
    period: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    progress: HhSyncProgress | None = None
    result: HhSyncResponse | None = None
    error_message: str | None = None
    attempts: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


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


def _job_to_out(row: HhSyncJobRow) -> HhSyncJobOut:
    params = row.params or {}
    return HhSyncJobOut(
        id=row.id,
        status=row.status,
        window=params.get("window") or "all",
        period=params.get("period"),
        date_from=params.get("date_from"),
        date_to=params.get("date_to"),
        progress=HhSyncProgress.model_validate(row.progress) if row.progress else None,
        result=HhSyncResponse.model_validate(row.result) if row.result else None,
        error_message=row.error_message,
        attempts=int(row.attempts or 0),
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


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


@router.post("/sync", response_model=HhSyncAccepted, status_code=202)
def hh_sync(
    body: HhSyncRequest = HhSyncRequest(),
    admin: UserRow = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> HhSyncAccepted | JSONResponse:
    """Поставить HH sync в очередь: 202 ``{job_id}``, 409 — sync уже активен."""
    actor = idempotency.actor_for_user(admin.id)
    scope = idempotency.SCOPE_HH_SYNC
    req_hash = idempotency.request_fingerprint(body.model_dump(mode="json"))
    begun = idempotency.begin(key=idempotency_key, scope=scope, actor=actor, request_hash=req_hash)
    if begun.cached is not None:
        return JSONResponse(status_code=begun.cached.status_code, content=begun.cached.body)
    if begun.in_progress:
        raise AppError(
            CODE_IDEMPOTENCY_IN_PROGRESS,
            "Запрос с этим Idempotency-Key ещё выполняется",
            status_code=409,
            retryable=True,
        )

    try:
        try:
            resolved = resolve_hh_search_window(
                window=body.window,
                date_from=body.date_from,
                date_to=body.date_to,
            )
        except HhSyncWindowError as exc:
            raise http_error(422, CODE_VALIDATION, str(exc)) from exc

        cfg = load_config(settings.config_path)
        try:
            hh_sync_service.ensure_hh_ready(settings, cfg.hh)
        except HhSyncNotConfigured as exc:
            raise http_error(503, CODE_UPSTREAM_UNAVAILABLE, str(exc), retryable=True) from exc

        job_id = hh_sync_job_service.start_hh_sync_job(resolved, requested_by=admin.id)
    except Exception:
        idempotency.abandon(key=idempotency_key, scope=scope, actor=actor)
        raise

    resp = HhSyncAccepted(job_id=job_id, status="queued")
    # Резерв закрывается сразу: повтор с тем же ключом вернёт тот же job_id, а не второй sync.
    idempotency.complete(
        key=idempotency_key,
        scope=scope,
        actor=actor,
        status_code=202,
        body=resp.model_dump(mode="json"),
        request_hash=req_hash,
    )
    return resp


@router.get("/sync-jobs", response_model=list[HhSyncJobOut])
def list_hh_sync_jobs(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: UserRow = Depends(require_admin),
) -> list[HhSyncJobOut]:
    """Последние sync, новые первыми: UI показывает активный или последний после перезагрузки."""
    return [_job_to_out(row) for row in hh_jobs.list_hh_sync_jobs(db, limit=limit)]


@router.get("/sync-jobs/{job_id}", response_model=HhSyncJobOut)
def get_hh_sync_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: UserRow = Depends(require_admin),
) -> HhSyncJobOut:
    row = hh_jobs.get_hh_sync_job(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="HH sync job not found")
    return _job_to_out(row)


# --- SSE ----------------------------------------------------------------------

_NOT_FOUND = sse_event("error", json.dumps({"detail": "HH sync job not found"}))


def _load_job_out(job_id: uuid.UUID) -> HhSyncJobOut | None:
    """Синхронное чтение — из стрима только через ``run_in_threadpool``, не в event loop."""
    with session_scope() as session:
        row = hh_jobs.get_hh_sync_job(session, job_id)
        return _job_to_out(row) if row is not None else None


def _status_line(out: HhSyncJobOut) -> str:
    return sse_event("status", out.model_dump_json())


def _terminal(out: HhSyncJobOut) -> bool:
    return out.status in hh_jobs.TERMINAL_STATUSES


async def _sse_hh_sync(job_id: uuid.UUID) -> AsyncIterator[str]:
    initial = await run_in_threadpool(_load_job_out, job_id)
    if initial is None:
        yield _NOT_FOUND
        return
    yield _status_line(initial)
    if _terminal(initial):
        yield SSE_DONE
        return
    if (get_settings().redis_url or "").strip():
        try:
            async for chunk in _sse_hh_sync_redis(job_id):
                yield chunk
            return
        except PubSubUnavailable as e:
            _LOG.warning("SSE HH sync %s: Redis pub/sub недоступен (%s) — опрос БД", job_id, e)
    async for chunk in _sse_hh_sync_db_poll(job_id, last=initial):
        yield chunk


async def _sse_hh_sync_db_poll(job_id: uuid.UUID, *, last: HhSyncJobOut) -> AsyncIterator[str]:
    """Опрос БД раз в секунду: статус при изменении, до completed/failed."""
    heartbeat = Ticker(HEARTBEAT_INTERVAL_S)
    while True:
        await asyncio.sleep(DB_POLL_INTERVAL_S)
        out = await run_in_threadpool(_load_job_out, job_id)
        if out is None:
            yield _NOT_FOUND
            return
        if out != last:
            last = out
            heartbeat.reset()
            yield _status_line(out)
        if _terminal(out):
            yield SSE_DONE
            return
        if heartbeat.due():
            yield SSE_PING


async def _sse_hh_sync_redis(job_id: uuid.UUID) -> AsyncIterator[str]:
    heartbeat = Ticker(HEARTBEAT_INTERVAL_S)
    db_check = Ticker(IDLE_DB_CHECK_S)
    async with get_pubsub_hub().subscribe(channel_for(job_id)) as sub:
        while True:
            payload = await sub.get(timeout=PUBSUB_WAIT_S)
            if payload is None and not db_check.due():
                if heartbeat.due():
                    yield SSE_PING
                continue
            # Событие (или редкая сверка в тишине): полный статус — из БД.
            out = await run_in_threadpool(_load_job_out, job_id)
            if out is None:
                yield _NOT_FOUND
                return
            if payload is not None or _terminal(out):
                heartbeat.reset()
                yield _status_line(out)
            if _terminal(out):
                yield SSE_DONE
                return


@sse_router.get("/sync-jobs/{job_id}/events", dependencies=[Depends(verify_api_key_sse)])
def hh_sync_job_events(
    job_id: uuid.UUID,
    authorization: str | None = Depends(bearer_token),
    access_token: str | None = Query(
        default=None,
        description="JWT для SSE-эндпоинтов (EventSource не шлёт заголовки)",
        include_in_schema=False,
    ),
) -> StreamingResponse:
    """SSE: события ``status`` (джоба целиком: статус, прогресс, итог) до ``done``. Только admin."""
    with session_scope() as session:
        user = _user_from_token(session, _extract_token(authorization, access_token))
        if user is None:
            raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
        if not user.is_admin:
            raise http_error(403, CODE_FORBIDDEN, "Admin only")
        if hh_jobs.get_hh_sync_job(session, job_id) is None:
            raise HTTPException(status_code=404, detail="HH sync job not found")
    return StreamingResponse(_sse_hh_sync(job_id), media_type="text/event-stream", headers=SSE_HEADERS)
