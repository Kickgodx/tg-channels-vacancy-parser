from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db, require_admin, verify_api_key
from backend.api.params import Page, PageSize, check_list_depth, vacancy_filters
from backend.db import repository as repo
from backend.db.aggregate_cache import cached_aggregate
from backend.db.models import UserRow
from backend.db.vacancy_metrics import aggregate_vacancy_metrics
from backend.db.vacancy_repository import counted_vacancies
from backend.errors import CODE_IDEMPOTENCY_IN_PROGRESS, CODE_RATE_LIMITED, CODE_VALIDATION, AppError, http_error
from backend.schemas import (
    LlmJobAccepted,
    VacancyImportRequest,
    VacancyImportResponse,
    VacancyListResponse,
    VacancyQuery,
    VacancySortField,
    VacancySortOrder,
)
from backend.services import export_service, idempotency, import_service
from backend.services.llm_job_service import KIND_IMPORT, LlmJobUserLimitError, llm_job_runner
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.vacancy import Vacancy

router = APIRouter(
    prefix="/vacancies",
    tags=["vacancies"],
    dependencies=[Depends(verify_api_key), Depends(get_current_user)],
)


@router.post(
    "/import",
    response_model=None,
    responses={
        200: {"model": VacancyImportResponse},
        202: {"model": LlmJobAccepted},
    },
)
def import_vacancies(
    body: VacancyImportRequest,
    db: Session = Depends(get_db),
    user: UserRow = Depends(require_admin),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Импорт вакансий из текста: rules + LLM-разбор и запись в БД (как в прогоне). Только admin.

    Без LLM (AI выключен или все тексты уже в базе) — сразу 200 с итогом. Нужен LLM —
    202 ``{job_id}``: разбор идёт LLM-джобой, итог по текстам растёт в
    ``GET /llm-jobs/{id}`` (``import_result``) и приходит в её SSE.
    """
    actor = idempotency.actor_for_user(user.id)
    scope = idempotency.SCOPE_IMPORT
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
        texts = [t.strip() for t in body.texts]
        if not any(texts):
            raise http_error(400, CODE_VALIDATION, "Пустой текст вакансии")
        source_url = (body.source_url or "").strip() or None
        if import_service.needs_llm(db, texts, source_url=source_url):
            try:
                queued = llm_job_runner.enqueue(
                    user_id=user.id,
                    kind=KIND_IMPORT,
                    params=import_service.job_params(texts, source_url),
                )
            except LlmJobUserLimitError as e:
                raise http_error(429, CODE_RATE_LIMITED, str(e), retryable=True) from e
            status_code = 202
            content = LlmJobAccepted(job_id=queued.job_id, status="queued").model_dump(mode="json")
        else:
            result = import_service.import_vacancy_texts(db, texts, source_url=source_url)
            status_code = 200
            content = import_service.to_import_response(result).model_dump(mode="json")
    except Exception:
        idempotency.abandon(key=idempotency_key, scope=scope, actor=actor)
        raise

    idempotency.complete(
        key=idempotency_key,
        scope=scope,
        actor=actor,
        status_code=status_code,
        body=content,
        request_hash=req_hash,
    )
    return JSONResponse(status_code=status_code, content=content)


@router.get("/export.xlsx")
def export_vacancies_xlsx(
    filters: VacancyQuery = Depends(vacancy_filters),
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Excel вакансий в БД с учётом фильтров (как в списке), не больше ``EXPORT_MAX_ROWS`` строк."""
    query = filters.model_copy(update={"sort_by": sort_by, "sort_order": sort_order})
    limit = export_service.export_row_limit()
    total = counted_vacancies(db, query)
    metrics = aggregate_vacancy_metrics(db, query)
    return export_service.xlsx_response(
        repo.iter_export_vacancies(db, query, limit=limit),
        total=total,
        limit=limit,
        metrics=metrics,
        filename="vacancies.xlsx",
    )


@router.get("/metrics", response_model=MetricsSummary)
def vacancy_metrics(
    query: VacancyQuery = Depends(vacancy_filters),
    db: Session = Depends(get_db),
) -> MetricsSummary:
    """Агрегаты для графиков (SQL GROUP BY, без загрузки всех вакансий)."""
    return cached_aggregate("vacancy_metrics", query, lambda: aggregate_vacancy_metrics(db, query))


@router.get("", response_model=VacancyListResponse)
def list_vacancies(
    filters: VacancyQuery = Depends(vacancy_filters),
    page: Page = 1,
    page_size: PageSize = 50,
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    resume_id: int | None = None,
    db: Session = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> VacancyListResponse:
    check_list_depth(page, page_size)
    if resume_id is not None:
        resume = repo.get_resume(db, resume_id)
        if resume is None or resume.user_id != user.id:
            raise HTTPException(status_code=404, detail="Resume not found")
    query = filters.model_copy(
        update={
            "page": page,
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "resume_id": resume_id,
        }
    )
    items, total = repo.list_vacancies(db, query)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{vacancy_id}", response_model=Vacancy)
def get_vacancy(vacancy_id: str, db: Session = Depends(get_db)) -> Vacancy:
    """Полная вакансия (с raw/cleaned_text) для карточки/модалки."""
    vacancy = repo.get_vacancy(db, vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return vacancy
