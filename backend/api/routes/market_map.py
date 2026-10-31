"""Карта рынка: вакансии на плоскости по смыслу, ниши и место резюме."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend import maintenance
from backend.api.deps import CurrentUser, get_current_user, get_db, require_admin, verify_api_key
from backend.api.routes.resumes import _get_own_resume
from backend.db import maintenance_requests
from backend.errors import CODE_VALIDATION, http_error
from backend.schemas import MarketMapCluster, MarketMapOut, MarketMapResume
from backend.services import market_map_service
from backend.services.embedding_service import EmbeddingsUnavailable

router = APIRouter(prefix="/market-map", tags=["market-map"], dependencies=[Depends(verify_api_key)])


def _map_out(db: Session, *, resume_id: int | None, user: CurrentUser) -> MarketMapOut:
    pending = maintenance_requests.pending_since(db, maintenance_requests.TASK_MARKET_MAP) is not None
    m = market_map_service.latest_map(db)
    if m is None:
        return MarketMapOut(available=False, rebuild_pending=pending)
    rows = market_map_service.map_points(db, m.id)
    out = MarketMapOut(
        available=True,
        created_at=m.created_at,
        model=m.model,
        points=len(rows),
        window_days=(m.params or {}).get("window_days"),
        clusters=[MarketMapCluster.model_validate(c) for c in m.clusters or []],
        ids=[r[0] for r in rows],
        x=[round(r[1], 4) for r in rows],
        y=[round(r[2], 4) for r in rows],
        cluster=[r[3] for r in rows],
        role=[(r[4] or [None])[0] for r in rows],
        grade=[r[5] if r[5] and r[5] != "unknown" else None for r in rows],
        salary=[round(r[6]) if r[6] is not None else None for r in rows],
        company=[r[7] for r in rows],
        archived=[r[8] is not None for r in rows],
    )
    out.rebuild_pending = pending
    if resume_id is not None:
        resume = _get_own_resume(db, resume_id, user)
        try:
            pos = market_map_service.resume_position(db, resume, m)
        except EmbeddingsUnavailable as e:
            out.resume_error = f"Резюме не поставить на карту: {e}"
        else:
            out.resume = MarketMapResume.model_validate(pos) if pos else None
    return out


@router.get("", response_model=MarketMapOut)
def get_market_map(
    resume_id: int | None = Query(default=None, description="Поставить на карту резюме пользователя."),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> MarketMapOut:
    """Последняя посчитанная карта; ``available=false`` — её ещё нет."""
    return _map_out(db, resume_id=resume_id, user=user)


@router.post("/rebuild", response_model=MarketMapOut, status_code=202)
def rebuild_market_map(db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)) -> MarketMapOut:
    """Запросить пересчёт карты; только админ. Ответ — текущая карта с ``rebuild_pending``.

    Считает фоновое обслуживание (лидер API или ``jobdesk-maintenance-worker``), не этот запрос:
    t-SNE на тысячах точек — минуты CPU. Готово, когда ``GET /market-map`` вернёт
    ``rebuild_pending=false``. Мало вакансий с эмбеддингами — 422 сразу.
    """
    if not market_map_service.has_enough_points(db):
        raise http_error(422, CODE_VALIDATION, f"Мало вакансий с эмбеддингами (нужно {market_map_service.MIN_POINTS})")
    maintenance_requests.request_task(db, maintenance_requests.TASK_MARKET_MAP)
    db.commit()
    if maintenance.runs_here_inprocess():
        maintenance.kick_requested_map_build()
    return _map_out(db, resume_id=None, user=user)
