"""Статус фоновых LLM-джоб (мэтчинг / сопроводительные письма) + SSE."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import _extract_token, _user_from_token, get_current_user, get_db
from backend.api.routes.resumes import _letter_to_out, _match_to_out
from backend.db import repository as repo
from backend.db.models import UserRow
from backend.db.session import session_scope
from backend.errors import CODE_NOT_AUTHENTICATED, http_error
from backend.observability import LLM_JOB_SSE_CONNECTIONS
from backend.schemas import LlmJobOut
from backend.services.llm_job_events import channel_for
from backend.services.llm_job_service import KIND_COVER_LETTER, KIND_MATCH
from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-jobs", tags=["llm-jobs"])


def _job_to_out(db: Session, row) -> LlmJobOut:
    match_out = None
    letter_out = None
    if row.status == "done":
        if row.kind == KIND_MATCH:
            m = repo.get_vacancy_match(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if m is not None:
                match_out = _match_to_out(m, cached=False)
        elif row.kind == KIND_COVER_LETTER:
            c = repo.get_cover_letter(db, resume_id=row.resume_id, vacancy_id=row.vacancy_id)
            if c is not None:
                letter_out = _letter_to_out(c, cached=False)

    return LlmJobOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        resume_id=row.resume_id,
        vacancy_id=row.vacancy_id,
        error_message=row.error_message,
        match=match_out,
        cover_letter=letter_out,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


@router.get("/{job_id}", response_model=LlmJobOut)
def get_llm_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> LlmJobOut:
    row = repo.get_llm_job(db, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_out(db, row)


async def _sse_llm_job(job_id: uuid.UUID, user_id: int) -> AsyncIterator[str]:
    LLM_JOB_SSE_CONNECTIONS.inc()
    try:
        with session_scope() as session:
            row = repo.get_llm_job(session, job_id)
            if row is None or row.user_id != user_id:
                yield f"event: error\ndata: {json.dumps({'detail': 'Job not found'})}\n\n"
                return
            initial = _job_to_out(session, row)
        yield f"event: status\ndata: {initial.model_dump_json()}\n\n"
        if initial.status in ("done", "error"):
            yield "event: done\ndata: {}\n\n"
            return

        settings = get_settings()
        redis_url = (settings.redis_url or "").strip()
        if not redis_url:
            # Без Redis — короткий poll fallback на сервере
            for _ in range(300):
                await asyncio.sleep(1.0)
                with session_scope() as session:
                    row = repo.get_llm_job(session, job_id)
                    if row is None:
                        break
                    out = _job_to_out(session, row)
                yield f"event: status\ndata: {out.model_dump_json()}\n\n"
                if out.status in ("done", "error"):
                    yield "event: done\ndata: {}\n\n"
                    return
            return

        from backend.queue.rq_conn import get_redis_connection

        conn = get_redis_connection()
        pubsub = conn.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel_for(job_id))
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
                    with session_scope() as session:
                        row = repo.get_llm_job(session, job_id)
                        if row is None:
                            yield f"event: error\ndata: {json.dumps({'detail': 'Job not found'})}\n\n"
                            return
                        out = _job_to_out(session, row)
                    yield f"event: status\ndata: {out.model_dump_json()}\n\n"
                    if out.status in ("done", "error") or payload.get("status") in ("done", "error"):
                        yield "event: done\ndata: {}\n\n"
                        return
                else:
                    # heartbeat / re-check terminal state
                    with session_scope() as session:
                        row = repo.get_llm_job(session, job_id)
                        if row is not None and row.status in ("done", "error"):
                            out = _job_to_out(session, row)
                            yield f"event: status\ndata: {out.model_dump_json()}\n\n"
                            yield "event: done\ndata: {}\n\n"
                            return
                await asyncio.sleep(0)
        finally:
            try:
                pubsub.unsubscribe(channel_for(job_id))
                pubsub.close()
            except Exception:
                pass
    finally:
        LLM_JOB_SSE_CONNECTIONS.dec()


@router.get("/{job_id}/events")
async def llm_job_events(
    job_id: uuid.UUID,
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(
        default=None,
        description="JWT для SSE-эндпоинтов (EventSource не шлёт заголовки)",
        include_in_schema=False,
    ),
) -> StreamingResponse:
    """SSE: статусы queued→running→done/error. Auth: Bearer или ``?access_token=``.

    Auth/ownership — короткая ``session_scope``; стрим не держит ``get_db``.
    """
    with session_scope() as session:
        user = _user_from_token(session, _extract_token(authorization, access_token))
        if user is None:
            raise http_error(401, CODE_NOT_AUTHENTICATED, "Not authenticated")
        user_id = user.id
        row = repo.get_llm_job(session, job_id)
        if row is None or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        _sse_llm_job(job_id, user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
