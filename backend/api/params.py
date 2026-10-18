"""Query-параметры фильтров вакансий (общие для списка, метрик, аналитики и экспорта).

Типы и границы объявлены на уровне FastAPI: ``page=0``, ``page_size=1000`` или
``date_from=abc`` дают 422 с описанием поля, а не 500 из ``VacancyQuery`` внутри
обработчика.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import Query
from pydantic import BeforeValidator

from backend.errors import CODE_VALIDATION, http_error
from backend.schemas import VacancyQuery

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
    """422 для ``sort_by=match_score`` без резюме: иначе список молча сортировался бы по дате."""
    if sort_by == "match_score" and resume_id is None:
        raise http_error(422, CODE_VALIDATION, "Сортировка по оценке (match_score) требует resume_id.")


def vacancy_filters(
    source_channel: Annotated[str | None, Query(max_length=128)] = None,
    grade: Annotated[str | None, Query(max_length=32)] = None,
    specialization: Annotated[list[str], Query(max_length=50)] = [],  # noqa: B006 — FastAPI копирует default
    work_format: Annotated[str | None, Query(max_length=32)] = None,
    date_from: Annotated[date | None, _EmptyAsNone, Query(description="YYYY-MM-DD")] = None,
    date_to: Annotated[date | None, _EmptyAsNone, Query(description="YYYY-MM-DD")] = None,
    has_salary: bool | None = None,
    q: Annotated[str | None, Query(max_length=500)] = None,
    include_archived: bool = False,
) -> VacancyQuery:
    """Фильтры корпуса без пагинации и сортировки (их добавляет обработчик списка)."""
    return VacancyQuery(
        source_channel=source_channel,
        grade=grade,
        specializations=specialization,
        work_format=work_format,
        date_from=date_from,
        date_to=date_to,
        has_salary=has_salary,
        q=q,
        include_archived=include_archived,
    )
