"""Статус фоновых LLM-джоб (мэтчинг / сопроводительные письма / импорт) + SSE."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.api.deps import (
    CurrentUser,
    SseCredentials,
    authenticate_sse,
    get_current_user,
    get_db,
    sse_credentials,
    verify_api_key,
)
from backend.api.routes.resumes import _letter_to_out, _match_to_out, _tailoring_to_out
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
from backend.db import repository as repo
from backend.db.llm_job_repository import TERMINAL_LLM_STATUSES
from backend.db.session import session_scope
from backend.observability import LLM_JOB_SSE_CONNECTIONS
from backend.schemas import LlmJobOut
from backend.services import import_service, llm_job_service, reextract_service
from backend.services.llm_job_events import channel_for
from backend.services.llm_job_service import (
    KIND_COVER_LETTER,
    KIND_IMPORT,
    KIND_MATCH,
    KIND_REEXTRACT,
    KIND_RESUME_TAILOR,
)
from backend.services.llm_result_freshness import current_ai_config
from backend.services.pubsub_hub import PubSubUnavailable, get_pubsub_hub
from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

# Поток событий — в этом же роутере, но ключ проверяет сам (тикет — без ключа):
# X-API-Key ставится на маршруты, а не на весь роутер.
router = APIRouter(prefix="/llm-jobs", tags=["llm-jobs"])


def _job_to_out(db: Session, row) -> LlmJobOut:
    match_out = None
    letter_out = None
    tailoring_out = None
    import_out = None
    reextract_out = None
    if row.kind == KIND_REEXTRACT:
        if row.result:
            reextract_out = reextract_service.reextract_response(row.result)
    elif row.kind == KIND_IMPORT:
        # Частичный итог тоже отдаём: по нему UI показывает прогресс импорта.
        if row.result:
            import_out = import_service.to_import_response(import_service.import_result_from_job(db, row.result))
    elif row.status == "done":
        if row.kind == KIND_MATCH:
            m = repo.get_vacancy_match(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if m is not None:
                match_out = _match_to_out(m, cached=False, ai=current_ai_config())
        elif row.kind == KIND_COVER_LETTER:
            c = repo.get_cover_letter(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if c is not None:
                letter_out = _letter_to_out(c, cached=False, ai=current_ai_config())
        elif row.kind == KIND_RESUME_TAILOR:
            t = repo.get_resume_tailoring(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if t is not None:
                tailoring_out = _tailoring_to_out(t, cached=False, ai=current_ai_config())

    return LlmJobOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        resume_id=row.resume_id,
        vacancy_id=row.vacancy_id,
        error_message=row.error_message,
        match=match_out,
        cover_letter=letter_out,
        resume_tailoring=tailoring_out,
        import_result=import_out,
        reextract_result=reextract_out,
        cancel_requested=bool(row.cancel_requested),
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/{job_id}", response_model=LlmJobOut, dependencies=[Depends(verify_api_key)])
def get_llm_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> LlmJobOut:
    row = repo.get_llm_job(db, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_out(db, row)


@router.post("/{job_id}/cancel", response_model=LlmJobOut, dependencies=[Depends(verify_api_key)])
def cancel_llm_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> LlmJobOut:
    """Отменить свою задачу; ответ — её текущее состояние (и для уже завершённой)."""
    if not llm_job_service.cancel_llm_job(job_id, user_id=user.id):
        raise HTTPException(status_code=404, detail="Job not found")
    db.expire_all()
    return _job_to_out(db, repo.get_llm_job(db, job_id))


_TERMINAL_JOB_STATUSES = TERMINAL_LLM_STATUSES


def _load_job_out(job_id: uuid.UUID, user_id: int) -> LlmJobOut | None:
    """Синхронное чтение — из стрима только через ``run_in_threadpool``, не в event loop."""
    with session_scope() as session:
        row = repo.get_llm_job(session, job_id)
        if row is None or row.user_id != user_id:
            return None
        return _job_to_out(session, row)


def _status_line(out: LlmJobOut) -> str:
    return sse_event("status", out.model_dump_json())


_NOT_FOUND = sse_event("error", json.dumps({"detail": "Job not found"}))


async def _sse_llm_job(job_id: uuid.UUID, user_id: int) -> AsyncIterator[str]:
    LLM_JOB_SSE_CONNECTIONS.inc()
    try:
        initial = await run_in_threadpool(_load_job_out, job_id, user_id)
        if initial is None:
            yield _NOT_FOUND
            return
        yield _status_line(initial)
        if initial.status in _TERMINAL_JOB_STATUSES:
            yield SSE_DONE
            return

        if (get_settings().redis_url or "").strip():
            try:
                async for chunk in _sse_llm_job_redis(job_id, user_id):
                    yield chunk
                return
            except PubSubUnavailable as e:
                _LOG.warning("SSE LLM-джобы %s: Redis pub/sub недоступен (%s) — опрос БД", job_id, e)
        async for chunk in _sse_llm_job_db_poll(job_id, user_id, last=initial):
            yield chunk
    finally:
        LLM_JOB_SSE_CONNECTIONS.dec()


async def _sse_llm_job_db_poll(job_id: uuid.UUID, user_id: int, *, last: LlmJobOut) -> AsyncIterator[str]:
    """Опрос БД раз в секунду: статус при изменении, до done/error."""
    heartbeat = Ticker(HEARTBEAT_INTERVAL_S)
    while True:
        await asyncio.sleep(DB_POLL_INTERVAL_S)
        out = await run_in_threadpool(_load_job_out, job_id, user_id)
        if out is None:
            yield _NOT_FOUND
            return
        if out != last:
            last = out
            heartbeat.reset()
            yield _status_line(out)
        if out.status in _TERMINAL_JOB_STATUSES:
            yield SSE_DONE
            return
        if heartbeat.due():
            yield SSE_PING


async def _sse_llm_job_redis(job_id: uuid.UUID, user_id: int) -> AsyncIterator[str]:
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
            out = await run_in_threadpool(_load_job_out, job_id, user_id)
            if out is None:
                yield _NOT_FOUND
                return
            terminal = out.status in _TERMINAL_JOB_STATUSES
            if payload is not None or terminal:
                heartbeat.reset()
                yield _status_line(out)
            if terminal or (payload or {}).get("status") in _TERMINAL_JOB_STATUSES:
                yield SSE_DONE
                return


@router.get("/{job_id}/events")
def llm_job_events(job_id: uuid.UUID, creds: SseCredentials = Depends(sse_credentials)) -> StreamingResponse:
    """SSE: статусы queued→running→done/error. Auth: ``?ticket=`` или Bearer.

    Обработчик синхронный (auth/ownership в threadpool, не в event loop); стрим не
    держит ``get_db`` — читает БД короткими сессиями.
    """
    with session_scope() as session:
        user_id = authenticate_sse(session, creds).id
        row = repo.get_llm_job(session, job_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        _sse_llm_job(job_id, user_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
