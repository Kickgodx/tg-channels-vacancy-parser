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


async def _sse_events(run_id: uuid.UUID) -> AsyncIterator[str]:
    q = await progress_bus.subscribe(run_id)
    try:
        while True:
            snap = await q.get()
            if snap is None:
                yield "event: done\ndata: {}\n\n"
                break
            preview = [
                {
                    "id": v.id,
                    "source_channel": v.source_channel,
                    "post_url": v.post_url,
                    "post_date": v.post_date.isoformat() if v.post_date else None,
                    "work_format": v.work_format,
                    "detected_salary_avg": v.detected_salary_avg,
                    "salary_currency": v.salary_currency,
                    "detected_specializations": v.detected_specializations,
                    "cleaned_text": (v.cleaned_text or "")[:300],
                }
                for v in (snap.vacancies or [])[-30:]
            ]
            payload = {
                "posts_processed": snap.posts_processed,
                "posts_total": snap.posts_total,
                "vacancies_found": snap.vacancies_found,
                "phase": snap.phase,
                "ai_llm_disabled": snap.ai_llm_disabled,
                "ai_llm_disabled_reason": snap.ai_llm_disabled_reason,
                "ai_tokens_in": snap.ai_tokens_in,
                "ai_tokens_out": snap.ai_tokens_out,
                "ai_skipped_score": snap.ai_skipped_score,
                "vacancies_preview": preview,
            }
            yield f"event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0)
    finally:
        progress_bus.unsubscribe(run_id, q)


_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


async def _sse_done_only() -> AsyncIterator[str]:
    yield "event: done\ndata: {}\n\n"


@router.get("/{run_id}/events")
async def run_events(run_id: uuid.UUID, db: Session = Depends(get_db)) -> StreamingResponse:
    row = repo.get_parse_run(db, run_id)
    if row is None and run_registry.get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    if row is not None and row.status in _TERMINAL_RUN_STATUSES:
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
