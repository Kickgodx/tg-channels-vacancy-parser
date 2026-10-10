from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_db, verify_api_key
from backend.config_builder import build_config
from backend.db import repository as repo
from backend.db.repository import metrics_from_run
from backend.db.session import session_scope
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
from backend.services import run_service
from backend.services.parse_progress_serialize import snapshot_to_progress_dict
from backend.services.parse_run_events import channel_for
from backend.services.progress_bus import progress_bus
from backend.services.run_registry import run_registry
from backend.settings import get_settings
from tg_jobs_parser.config.loader import ConfigLoadError
from tg_jobs_parser.exporters.excel_exporter import export_excel_bytes
from tg_jobs_parser.exporters.json_exporter import export_json_bytes
from tg_jobs_parser.models.parse_result import ParseResult

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(verify_api_key)])


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
def create_run(body: RunCreateRequest, db: Session = Depends(get_db)) -> RunCreateResponse:
    settings = get_settings()
    try:
        config = build_config(settings.config_path, body)
    except ConfigLoadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        run_id = run_service.start_parse_run(config, body)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    _LOG.info(
        "Создан прогон %s: каналов=%s, AI=%s",
        run_id,
        len(body.channels) if body.channels else len(config.sources.channels),
        config.ai.enabled,
    )
    return RunCreateResponse(run_id=run_id, status="running")


@router.get("", response_model=list[RunSummary])
def list_runs(
    limit: int = 50,
    offset: int = 0,
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
def cancel_run(run_id: uuid.UUID) -> dict:
    if not run_service.cancel_run(run_id):
        raise HTTPException(status_code=404, detail="Run not running or not found")
    _LOG.info("Прогон %s: отмена запрошена через API", run_id)
    return {"ok": True, "run_id": str(run_id)}


@router.get("/{run_id}/vacancies", response_model=VacancyListResponse)
def run_vacancies(
    run_id: uuid.UUID,
    page: int = 1,
    page_size: int = 50,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    db: Session = Depends(get_db),
) -> VacancyListResponse:
    if repo.get_parse_run(db, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    q = VacancyQuery(page=page, page_size=page_size, sort_by=sort_by, sort_order=sort_order)
    items, total = repo.list_run_vacancies(db, run_id, q)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)


_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _sse_progress_line(payload: dict) -> str:
    return f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _sse_events(run_id: uuid.UUID) -> AsyncIterator[str]:
    with session_scope() as session:
        row = repo.get_parse_run(session, run_id)
        if row is None and run_registry.get(run_id) is None:
            yield f"event: error\ndata: {json.dumps({'detail': 'Run not found'})}\n\n"
            return
        if row is not None and row.status in _TERMINAL_RUN_STATUSES:
            yield "event: done\ndata: {}\n\n"
            return
        initial = dict(row.progress) if row is not None and row.progress else None

    if initial:
        yield _sse_progress_line(initial)

    settings = get_settings()
    redis_url = (settings.redis_url or "").strip()
    if redis_url:
        async for chunk in _sse_events_redis(run_id):
            yield chunk
    else:
        async for chunk in _sse_events_progress_bus(run_id):
            yield chunk


async def _sse_events_progress_bus(run_id: uuid.UUID) -> AsyncIterator[str]:
    q = await progress_bus.subscribe(run_id)
    try:
        while True:
            snap = await q.get()
            if snap is None:
                yield "event: done\ndata: {}\n\n"
                break
            payload = snapshot_to_progress_dict(snap)
            yield _sse_progress_line(payload)
            await asyncio.sleep(0)
    finally:
        progress_bus.unsubscribe(run_id, q)


async def _sse_events_redis(run_id: uuid.UUID) -> AsyncIterator[str]:
    from backend.queue.rq_conn import get_redis_connection

    conn = get_redis_connection()
    pubsub = conn.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(channel_for(run_id))
    loop = asyncio.get_running_loop()
    try:
        while True:
            message = await loop.run_in_executor(None, lambda: pubsub.get_message(timeout=1.0))
            if message and message.get("type") == "message":
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw) if isinstance(raw, str) else {}
                except json.JSONDecodeError:
                    payload = {}
                event = payload.pop("event", None)
                if event == "progress":
                    yield _sse_progress_line(payload)
                if event == "done":
                    yield "event: done\ndata: {}\n\n"
                    return
            else:
                with session_scope() as session:
                    row = repo.get_parse_run(session, run_id)
                    if row is not None and row.status in _TERMINAL_RUN_STATUSES:
                        yield "event: done\ndata: {}\n\n"
                        return
            await asyncio.sleep(0)
    finally:
        try:
            pubsub.unsubscribe(channel_for(run_id))
            pubsub.close()
        except Exception:
            pass


async def _sse_done_only() -> AsyncIterator[str]:
    yield "event: done\ndata: {}\n\n"


@router.get("/{run_id}/events")
async def run_events(run_id: uuid.UUID) -> StreamingResponse:
    """SSE прогресса прогона. Auth: X-API-Key (router). Сессия БД не держится на время стрима."""
    with session_scope() as session:
        row = repo.get_parse_run(session, run_id)
        if row is None and run_registry.get(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        terminal = row is not None and row.status in _TERMINAL_RUN_STATUSES
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if terminal:
        return StreamingResponse(
            _sse_done_only(),
            media_type="text/event-stream",
            headers=headers,
        )
    return StreamingResponse(
        _sse_events(run_id),
        media_type="text/event-stream",
        headers=headers,
    )


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


@router.get("/{run_id}/export.json")
def export_run_json(run_id: uuid.UUID, db: Session = Depends(get_db)):
    row = repo.get_parse_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    items = repo.list_all_run_vacancies(
        db,
        run_id,
        VacancyQuery(page=1, page_size=500, sort_by="post_date", sort_order="desc"),
    )
    result = _parse_result_from_run(row, items)
    data = export_json_bytes(result)
    return StreamingResponse(
        iter([data]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id}.json"'},
    )


@router.get("/{run_id}/export.xlsx")
def export_run_xlsx(run_id: uuid.UUID, db: Session = Depends(get_db)):
    row = repo.get_parse_run(db, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    items = repo.list_all_run_vacancies(
        db,
        run_id,
        VacancyQuery(page=1, page_size=500, sort_by="post_date", sort_order="desc"),
    )
    metrics = metrics_from_run(row)
    data = export_excel_bytes(items, metrics=metrics)
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="run_{run_id}.xlsx"'},
    )
