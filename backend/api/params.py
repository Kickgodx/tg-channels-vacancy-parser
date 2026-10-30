"""Query-параметры фильтров вакансий (общие для списка, метрик, аналитики и экспорта).

Типы и границы объявлены на уровне FastAPI: ``page=0``, ``page_size=1000`` или
``date_from=abc`` дают 422 с описанием поля, а не 500 из ``VacancyQuery`` внутри
обработчика.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Query
from pydantic import BeforeValidator

from backend.db.vacancy_filters import analytics_query
from backend.errors import CODE_VALIDATION, http_error
from backend.schemas import VacancyQuery, sort_pair_error

# Пустой параметр (``?date_from=``) — «не задан», как и раньше, а не ошибка формата.
_EmptyAsNone = BeforeValidator(lambda v: None if v == "" else v)

# OFFSET-пагинация: дальние страницы дороги, а UI до них не доходит.
MAX_PAGE = 10_000
# Глубина списка: OFFSET сканирует и отбрасывает все строки до страницы, поэтому
# листать можно только первые MAX_LIST_DEPTH позиций — дальше уточняют фильтры.
MAX_LIST_DEPTH = 10_000

Page = Annotated[int, Query(ge=1, le=MAX_PAGE)]
PageSize = Annotated[int, Query(ge=1, le=500)]


def check_list_depth(page: int, page_size: int) -> None:
    """422, если страница уходит дальше ``MAX_LIST_DEPTH`` позиций списка."""
    if page * page_size > MAX_LIST_DEPTH:
        raise http_error(
            422,
            CODE_VALIDATION,
            f"Список листается только до {MAX_LIST_DEPTH} позиций — уточните фильтры или смените сортировку.",
        )


def check_sort_needs_resume(sort_by: str, resume_id: int | None) -> None:
    """422 для ``match_score`` / ``resume_fit`` без резюме: иначе список молча сортировался бы по дате."""
    if sort_by in ("match_score", "resume_fit") and resume_id is None:
        raise http_error(422, CODE_VALIDATION, f"Сортировка {sort_by} требует resume_id.")


def check_sort_pair(sort_by: str, then_by: str | None) -> None:
    """422 для второй сортировки вне пары «дата ↔ зарплата ₽» (``model_copy`` валидатор не зовёт)."""
    error = sort_pair_error(sort_by, then_by)
    if error:
        raise http_error(422, CODE_VALIDATION, error)


def vacancy_filters(
    source_channel: Annotated[str | None, Query(max_length=128)] = None,
    grade: Annotated[str | None, Query(max_length=32)] = None,
    specialization: Annotated[list[str], Query(max_length=50)] = [],  # noqa: B006 — FastAPI копирует default
    technology: Annotated[list[str], Query(max_length=50)] = [],  # noqa: B006
    work_format: Annotated[str | None, Query(max_length=32)] = None,
    date_from: Annotated[date | None, _EmptyAsNone, Query(description="YYYY-MM-DD")] = None,
    date_to: Annotated[date | None, _EmptyAsNone, Query(description="YYYY-MM-DD")] = None,
    has_salary: bool | None = None,
    salary_estimates: bool = False,
    q: Annotated[str | None, Query(max_length=500)] = None,
    include_archived: bool = False,
    search_mode: Literal["text", "semantic"] = "text",
) -> VacancyQuery:
    """Фильтры корпуса без пагинации и сортировки (их добавляет обработчик списка).

    ``search_mode=semantic`` с ``q`` — вектор запроса считается здесь (провайдер эмбеддингов);
    недоступен — 503, а не тихий откат к полнотекстовому поиску.
    """
    semantic_vector = embedding_model = None
    if q and q.strip() and search_mode == "semantic":
        semantic_vector, embedding_model = _semantic_vector(q)
    return VacancyQuery(
        source_channel=source_channel,
        grade=grade,
        specializations=specialization,
        technologies=technology,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        has_salary=has_salary,
        salary_estimates=salary_estimates,
        q=q,
        include_archived=include_archived,
        search_mode=search_mode,
        semantic_vector=semantic_vector,
        embedding_model=embedding_model,
    )


def analytics_filters(query: Annotated[VacancyQuery, Depends(vacancy_filters)]) -> VacancyQuery:
    """Фильтры агрегатов (метрики, аналитика): оценки зарплаты в них не входят, флаг ``salary_estimates`` сброшен."""
    return analytics_query(query)


def _semantic_vector(q: str) -> tuple[list[float], str]:
    from backend.errors import CODE_UPSTREAM_UNAVAILABLE
    from backend.services.embedding_service import EmbeddingsUnavailable, current_model, query_embedding

    try:
        return query_embedding(q), current_model()
    except EmbeddingsUnavailable as e:
        raise http_error(503, CODE_UPSTREAM_UNAVAILABLE, f"Смысловой поиск недоступен: {e}", retryable=True) from e
