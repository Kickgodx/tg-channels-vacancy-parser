from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.api.deps import (
    _extract_token,
    _user_from_token,
    bearer_token,
    get_db,
    require_admin,
    verify_api_key,
    verify_api_key_sse,
)
from backend.api.params import Page, PageSize, check_list_depth
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
from backend.config_builder import build_config
from backend.db import repository as repo
from backend.db.models import UserRow
from backend.db.repository import metrics_from_run
from backend.db.session import session_scope
from backend.errors import (
    CODE_FORBIDDEN,
    CODE_IDEMPOTENCY_IN_PROGRESS,
    CODE_NOT_AUTHENTICATED,
    CODE_VALIDATION,
    AppError,
    http_error,
)
from backend.schemas import (
    RunCreateRequest,
    RunCreateResponse,
    RunDetail,
    RunSummary,
    VacancyListResponse,
    VacancyQuery,
    VacancySortField,
    VacancySortOrder,
)
from backend.services import export_service, idempotency, run_service
from backend.services.parse_run_events import channel_for
from backend.services.pubsub_hub import PubSubUnavailable, get_pubsub_hub
from backend.settings import get_settings
from tg_jobs_parser.config.loader import ConfigLoadError
from tg_jobs_parser.exporters.json_exporter import export_json_bytes
from tg_jobs_parser.models.parse_result import ParseResult

_LOG = logging.getLogger(__name__)

# Прогоны целиком (запуск, отмена, чтение, экспорт, SSE) — только admin.
router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(verify_api_key), Depends(require_admin)],
)
# SSE отдельно: ``?api_key=`` / ``?access_token=`` только здесь, не на всём /runs.
sse_router = APIRouter(prefix="/runs", tags=["runs"])


def _row_to_summary(row) -> RunSummary:
    metrics = metrics_from_run(row)
    unique = metrics.unique_vacancies if metrics else None
    return RunSummary(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        posts_fetched=row.posts_fetched,
        parse_warnings=row.parse_warnings,
        unique_vacancies=unique,
    )


@router.post("", response_model=RunCreateResponse, status_code=202)
def create_run(
    body: RunCreateRequest,
    admin: UserRow = Depends(require_admin),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RunCreateResponse | JSONResponse:
    actor = idempotency.ACTOR_API
    scope = idempotency.SCOPE_PARSE_RUN
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

    settings = get_settings()
    try:
        try:
            config = build_config(settings.config_path, body)
        except ConfigLoadError as e:
            raise http_error(400, CODE_VALIDATION, str(e)) from e
        run_id = run_service.start_parse_run(config, body)
    except Exception:
        idempotency.abandon(key=idempotency_key, scope=scope, actor=actor)
        raise

    _LOG.info(
        "Создан прогон %s (user=%s): каналов=%s, AI=%s",
        run_id,
        admin.id,
        len(body.channels) if body.channels else len(config.sources.channels),
        config.ai.enabled,
    )
    payload = RunCreateResponse(run_id=run_id, status="queued")
    body_out = payload.model_dump(mode="json")
    idempotency.complete(
        key=idempotency_key,
        scope=scope,
        actor=actor,
        status_code=202,
        body=body_out,
        request_hash=req_hash,
    )
    return payload


@router.get("", response_model=list[RunSummary])
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[RunSummary]:
    rows = repo.list_parse_runs(db, limit=limit, offset=offset)
    return [_row_to_summary(r) for r in rows]


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> RunDetail:
    row = repo.get_parse_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    summary = _row_to_summary(row)
    return RunDetail(
        **summary.model_dump(),
        config_snapshot=row.config_snapshot or {},
        run_params=row.run_params,
        metrics=metrics_from_run(row),
        error_message=row.error_message,
        progress=row.progress,
        progress_updated_at=row.progress_updated_at,
        cancel_requested=row.cancel_requested,
    )


@router.post("/{run_id}/cancel")
def cancel_run(run_id: uuid.UUID, admin: UserRow = Depends(require_admin)) -> dict:
    if not run_service.cancel_run(run_id):
        raise HTTPException(status_code=404, detail="Run not cancellable or not found")
    _LOG.info("Прогон %s: отмена запрошена через API (user=%s)", run_id, admin.id)
    return {"ok": True, "run_id": str(run_id)}


@router.get("/{run_id}/vacancies", response_model=VacancyListResponse)
def run_vacancies(
    run_id: uuid.UUID,
    page: Page = 1,
    page_size: PageSize = 50,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    db: Session = Depends(get_db),
) -> VacancyListResponse:
    check_list_depth(page, page_size)
    if repo.get_parse_run(db, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    q = VacancyQuery(page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order)
    items, total = repo.list_run_vacancies(db, run_id, q)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)


_TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})


def _sse_progress_line(payload: dict) -> str:
    return sse_event("progress", json.dumps(payload, ensure_ascii=False))


@dataclass(frozen=True)
class _RunState:
    status: str
    progress: dict | None
    progress_updated_at: datetime | None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_RUN_STATUSES


def _load_run_state(run_id: uuid.UUID) -> _RunState | None:
    """Синхронное чтение — из стрима только через ``run_in_threadpool``, не в event loop."""
    with session_scope() as session:
        row = repo.get_parse_run(session, run_id)
        if row is None:
            return None
        return _RunState(
            status=row.status,
            progress=dict(row.progress) if row.progress else None,
            progress_updated_at=row.progress_updated_at,
        )


async def _sse_events(run_id: uuid.UUID) -> AsyncIterator[str]:
    state = await run_in_threadpool(_load_run_state, run_id)
    if state is None:
        yield sse_event("error", json.dumps({"detail": "Run not found"}))
        return
    if state.terminal:
        yield SSE_DONE
        return
    if state.progress:
        yield _sse_progress_line(state.progress)

    if (get_settings().redis_url or "").strip():
        try:
            async for chunk in _sse_events_redis(run_id):
                yield chunk
            return
        except PubSubUnavailable as e:
            _LOG.warning("SSE прогона %s: Redis pub/sub недоступен (%s) — опрос БД", run_id, e)
    # Без Redis (или после его сбоя) — durable DB poll (SoT = parse_runs.progress).
    async for chunk in _sse_events_db_poll(run_id, last_updated=state.progress_updated_at):
        yield chunk


async def _sse_events_db_poll(run_id: uuid.UUID, *, last_updated: datetime | None) -> AsyncIterator[str]:
    """Опрос БД раз в секунду: progress при смене progress_updated_at, до терминального статуса."""
    heartbeat = Ticker(HEARTBEAT_INTERVAL_S)
    while True:
        await asyncio.sleep(DB_POLL_INTERVAL_S)
        state = await run_in_threadpool(_load_run_state, run_id)
        if state is None:
            return
        if state.progress_updated_at is not None and state.progress_updated_at != last_updated and state.progress:
            last_updated = state.progress_updated_at
            heartbeat.reset()
            yield _sse_progress_line(state.progress)
        if state.terminal:
            yield SSE_DONE
            return
        if heartbeat.due():
            yield SSE_PING


async def _sse_events_redis(run_id: uuid.UUID) -> AsyncIterator[str]:
    heartbeat = Ticker(HEARTBEAT_INTERVAL_S)
    db_check = Ticker(IDLE_DB_CHECK_S)
    async with get_pubsub_hub().subscribe(channel_for(run_id)) as sub:
        while True:
            payload = await sub.get(timeout=PUBSUB_WAIT_S)
            if payload is not None:
                event = payload.pop("event", None)
                if event == "progress":
                    heartbeat.reset()
                    yield _sse_progress_line(payload)
                elif event == "done":
                    yield SSE_DONE
                    return
                continue
            if db_check.due():
                state = await run_in_threadpool(_load_run_state, run_id)
                if state is None or state.terminal:
                    yield SSE_DONE
                    return
            if heartbeat.due():
                yield SSE_PING


async def _sse_done_only() -> AsyncIterator[str]:
    yield SSE_DONE


@sse_router.get("/{run_id}/events", dependencies=[Depends(verify_api_key_sse)])
def run_events(
    run_id: uuid.UUID,
    authorization: str | None = Depends(bearer_token),
    access_token: str | None = Query(
        default=None,
        description="JWT для SSE-эндпоинтов (EventSource не шлёт заголовки)",
        include_in_schema=False,
    ),
) -> StreamingResponse:
    """SSE прогресса прогона. Auth: Bearer или ``?access_token=``; только admin.

    Обработчик синхронный (auth в threadpool, не в event loop); стрим не держит
    ``get_db`` — читает БД короткими сессиями.
    """
    with session_scope() as session:
        user = _user_from_token(session, _extract_token(authorization, access_token))
        if user is None:
            raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
        if not user.is_admin:
            raise http_error(403, CODE_FORBIDDEN, "Admin only")
        row = repo.get_parse_run(session, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        terminal = row.status in _TERMINAL_RUN_STATUSES
    stream = _sse_done_only() if terminal else _sse_events(run_id)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


def _parse_result_from_run(row, vacancies) -> ParseResult:
    from tg_jobs_parser.models.metrics import MetricsSummary

    metrics = metrics_from_run(row) or MetricsSummary()
    return ParseResult(
        run_params=row.run_params or {},
        posts_fetched=row.posts_fetched,
        parse_warnings=row.parse_warnings,
        vacancies_all=vacancies,
        vacancies_unique=vacancies,
        duplicates=[],
        metrics=metrics,
        parsed_at=row.finished_at or row.created_at,
    )


_RUN_EXPORT_QUERY = VacancyQuery(page=1, page_size=500, sort_by="post_date", sort_order="desc")


@router.get("/{run_id}/export.json")
def export_run_json(run_id: uuid.UUID, db: Session = Depends(get_db)):
    """JSON прогона, не больше ``EXPORT_MAX_ROWS`` вакансий (при обрезке — ``export_truncated``)."""
    row = repo.get_parse_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    limit = export_service.export_row_limit()
    total = repo.count_run_vacancies(db, run_id)
    items = list(repo.iter_export_run_vacancies(db, run_id, _RUN_EXPORT_QUERY, limit=limit))
    result = _parse_result_from_run(row, items)
    note = export_service.truncation_note(total, limit)
    extra = {"export_truncated": {"exported": len(items), "total": total, "note": note}} if note else None
    data = export_json_bytes(result, extra=extra)
    headers = {"Content-Disposition": f'attachment; filename="run_{run_id}.json"'}
    if note:
        headers["X-Export-Truncated"] = f"{len(items)}/{total}"
    return StreamingResponse(iter([data]), media_type="application/json", headers=headers)


@router.get("/{run_id}/export.xlsx")
def export_run_xlsx(run_id: uuid.UUID, db: Session = Depends(get_db)):
    row = repo.get_parse_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    limit = export_service.export_row_limit()
    return export_service.xlsx_response(
        repo.iter_export_run_vacancies(db, run_id, _RUN_EXPORT_QUERY, limit=limit),
        total=repo.count_run_vacancies(db, run_id),
        limit=limit,
        metrics=metrics_from_run(row),
        filename=f"run_{run_id}.xlsx",
    )
