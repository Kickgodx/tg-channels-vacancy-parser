from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import CurrentUser, get_current_user, get_db, require_admin, verify_api_key
from backend.api.params import (
    Page,
    PageSize,
    check_list_depth,
    check_sort_needs_resume,
    check_sort_pair,
    vacancy_filters,
)
from backend.db import repository as repo
from backend.db.aggregate_cache import cached_aggregate
from backend.db.company_profile import vacancy_flags
from backend.db.market_salary import market_salary
from backend.db.models import LlmJobRow
from backend.db.vacancy_metrics import aggregate_vacancy_metrics, technology_counts
from backend.db.vacancy_repository import counted_vacancies
from backend.errors import (
    CODE_IDEMPOTENCY_IN_PROGRESS,
    CODE_RATE_LIMITED,
    CODE_UPSTREAM_UNAVAILABLE,
    CODE_VALIDATION,
    AppError,
    http_error,
)
from backend.schemas import (
    LlmJobAccepted,
    MarketSalaryOut,
    TechnologyCount,
    TechnologyFacetResponse,
    VacancyFlags,
    VacancyImportRequest,
    VacancyImportResponse,
    VacancyListResponse,
    VacancyQuery,
    VacancyReextractInfo,
    VacancySortField,
    VacancySortOrder,
    VacancyThenSortField,
)
from backend.services import export_service, idempotency, import_service, reextract_service
from backend.services.llm_job_service import KIND_IMPORT, KIND_REEXTRACT, LlmJobUserLimitError, llm_job_runner
from backend.services.llm_result_freshness import current_ai_config
from backend.settings import get_settings
from tg_jobs_parser.config.loader import load_config
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
    user: CurrentUser = Depends(require_admin),
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


def _active_reextract_job_id(db: Session):
    # Одна джоба переразбора на всех: вторая разбирала бы те же вакансии.
    return db.scalar(
        select(LlmJobRow.id)
        .where(LlmJobRow.kind == KIND_REEXTRACT, LlmJobRow.status.in_(("queued", "running")))
        .order_by(LlmJobRow.created_at.desc())
        .limit(1)
    )


@router.get("/reextract", response_model=VacancyReextractInfo)
def reextract_info(
    db: Session = Depends(get_db),
    _user: CurrentUser = Depends(require_admin),
) -> VacancyReextractInfo:
    """Сколько вакансий без специализации ждут переразбора LLM. Только admin."""
    return VacancyReextractInfo(
        missing_role=reextract_service.count_missing_role(db),
        max_per_job=reextract_service.MAX_REEXTRACT_VACANCIES,
        ai_enabled=load_config(get_settings().config_path).ai.enabled,
        active_job_id=_active_reextract_job_id(db),
    )


@router.post("/reextract", status_code=202, response_model=LlmJobAccepted)
def start_reextract(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_admin),
) -> LlmJobAccepted:
    """Переразобрать LLM активные вакансии (не HH) без специализации. Только admin.

    202 ``{job_id}``: итог по вакансиям растёт в ``GET /llm-jobs/{id}`` (``reextract_result``).
    Уже идёт переразбор — 202 с его ``job_id``: повтор запроса новой джобы не ставит.
    """
    active = _active_reextract_job_id(db)
    if active is not None:
        return LlmJobAccepted(job_id=active, status="running")
    if not load_config(get_settings().config_path).ai.enabled:
        raise http_error(409, CODE_UPSTREAM_UNAVAILABLE, "AI выключен — переразбор вакансий невозможен.")
    ids = reextract_service.missing_role_vacancy_ids(db)
    if not ids:
        raise http_error(409, CODE_VALIDATION, "Вакансий без специализации нет — переразбирать нечего.")
    try:
        queued = llm_job_runner.enqueue(
            user_id=user.id,
            kind=KIND_REEXTRACT,
            params=reextract_service.job_params(ids),
        )
    except LlmJobUserLimitError as e:
        raise http_error(429, CODE_RATE_LIMITED, str(e), retryable=True) from e
    return LlmJobAccepted(job_id=queued.job_id, status="queued")


@router.get("/technologies", response_model=TechnologyFacetResponse)
def technology_facets(
    filters: VacancyQuery = Depends(vacancy_filters),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> TechnologyFacetResponse:
    """Технологии по числу вакансий под фильтрами — варианты фильтра «Технологии».

    Собственный выбор технологий не учитывается: иначе выбранная технология
    оставила бы в списке только саму себя и те, что встречаются вместе с ней.
    """
    query = filters.model_copy(update={"technologies": []})
    counts = cached_aggregate(
        "technology_facets", query, lambda: technology_counts(db, query, limit=limit), limit=limit
    )
    return TechnologyFacetResponse(items=[TechnologyCount(name=k, count=v) for k, v in counts.items()])


@router.get("/export.xlsx")
def export_vacancies_xlsx(
    filters: VacancyQuery = Depends(vacancy_filters),
    sort_by: VacancySortField = "post_date",
    sort_order: VacancySortOrder = "desc",
    then_by: VacancyThenSortField | None = None,
    then_order: VacancySortOrder = "desc",
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Excel вакансий в БД с учётом фильтров (как в списке), не больше ``EXPORT_MAX_ROWS`` строк."""
    check_sort_needs_resume(sort_by, None)  # резюме у экспорта нет
    check_sort_pair(sort_by, then_by)
    query = filters.model_copy(
        update={"sort_by": sort_by, "sort_order": sort_order, "then_by": then_by, "then_order": then_order}
    )
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
    then_by: VacancyThenSortField | None = None,
    then_order: VacancySortOrder = "desc",
    resume_id: int | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> VacancyListResponse:
    check_list_depth(page, page_size)
    check_sort_needs_resume(sort_by, resume_id)
    check_sort_pair(sort_by, then_by)
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
            "then_by": then_by,
            "then_order": then_order,
            "resume_id": resume_id,
        }
    )
    match_ai = current_ai_config() if resume_id is not None else None
    items, total = repo.list_vacancies(db, query, match_ai=match_ai)
    return VacancyListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{vacancy_id}", response_model=Vacancy)
def get_vacancy(vacancy_id: str, db: Session = Depends(get_db)) -> Vacancy:
    """Полная вакансия (с raw/cleaned_text) для карточки/модалки."""
    vacancy = repo.get_vacancy(db, vacancy_id)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return vacancy


@router.get("/{vacancy_id}/market-salary", response_model=MarketSalaryOut)
def vacancy_market_salary(vacancy_id: str, db: Session = Depends(get_db)) -> MarketSalaryOut:
    """Рыночная вилка: квантили зарплаты похожих вакансий (грейд, роль, технологии) за год."""
    row = repo.get_vacancy_row(db, vacancy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return market_salary(db, row)


@router.get("/{vacancy_id}/flags", response_model=VacancyFlags)
def get_vacancy_flags(vacancy_id: str, db: Session = Depends(get_db)) -> VacancyFlags:
    """Флаги вакансии по правилам: перепосты, давно висит, частый найм на роль, нет зарплаты."""
    row = repo.get_vacancy_row(db, vacancy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return vacancy_flags(db, row)
