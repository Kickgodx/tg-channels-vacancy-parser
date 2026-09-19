"""Общие фильтры списка вакансий и агрегатов метрик."""

from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import ColumnElement, Table, and_, cast, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import Select

from backend.db.models import VacancyRow
from backend.schemas import VacancyQuery


def _specializations_match(c, specs: list[str], dialect: str) -> ColumnElement[bool]:
    """Хотя бы одна из выбранных специализаций в JSON-массиве вакансии."""
    if dialect == "postgresql":
        return or_(
            *[
                cast(c.detected_specializations, JSONB).contains([spec])
                for spec in specs
            ]
        )
    je = func.json_each(c.detected_specializations).table_valued("value")
    return exists(select(literal(1)).select_from(je).where(je.c.value.in_(specs)))


def vacancy_filter_conditions(
    query: VacancyQuery,
    *,
    table: Table | None = None,
    dialect: str = "postgresql",
    include_duplicates: bool = False,
) -> list[ColumnElement[bool]]:
    """Условия WHERE для вакансий. table — Core-алиас (vacancies AS t), иначе ORM VacancyRow."""
    c = table.c if table is not None else VacancyRow
    conditions: list[ColumnElement[bool]] = []
    if not include_duplicates:
        conditions.append(c.is_duplicate.is_(False))
    if not query.include_archived:
        conditions.append(c.archived_at.is_(None))
    if query.source_channel:
        conditions.append(c.source_channel == query.source_channel.lstrip("@"))
    if query.grade:
        conditions.append(c.detected_grade == query.grade)
    if query.specializations:
        conditions.append(_specializations_match(c, query.specializations, dialect))
    if query.work_format:
        conditions.append(c.work_format == query.work_format)
    if query.date_from:
        dt_from = datetime.combine(query.date_from, time.min, tzinfo=timezone.utc)
        conditions.append(c.post_date >= dt_from)
    if query.date_to:
        dt_to = datetime.combine(query.date_to, time.max, tzinfo=timezone.utc)
        conditions.append(c.post_date <= dt_to)
    if query.q:
        conditions.append(c.cleaned_text.ilike(f"%{query.q}%"))
    if query.has_salary:
        conditions.append(
            or_(
                c.detected_salary_min.isnot(None),
                c.detected_salary_max.isnot(None),
                c.detected_salary_avg.isnot(None),
            )
        )
    return conditions


def apply_vacancy_filters(
    stmt: Select,
    query: VacancyQuery,
    *,
    table: Table | None = None,
    dialect: str = "postgresql",
    include_duplicates: bool = False,
) -> Select:
    """Ограничить выборку уникальными вакансиями и параметрами запроса."""
    conditions = vacancy_filter_conditions(
        query, table=table, dialect=dialect, include_duplicates=include_duplicates
    )
    if not conditions:
        return stmt
    return stmt.where(and_(*conditions))


def vacancy_count_stmt(query: VacancyQuery, *, dialect: str = "postgresql") -> Select:
    from sqlalchemy import func, select

    base = select(func.count(VacancyRow.id))
    return apply_vacancy_filters(base, query, dialect=dialect)
