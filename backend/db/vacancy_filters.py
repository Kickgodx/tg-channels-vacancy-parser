"""Общие фильтры списка вакансий и агрегатов метрик."""

from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import ColumnElement, and_
from sqlalchemy.sql import Select

from backend.db.models import VacancyRow
from backend.schemas import VacancyQuery


def apply_vacancy_filters(stmt: Select, query: VacancyQuery) -> Select:
    """Ограничить выборку уникальными вакансиями и параметрами запроса."""
    conditions: list[ColumnElement[bool]] = [VacancyRow.is_duplicate.is_(False)]
    if query.source_channel:
        conditions.append(VacancyRow.source_channel == query.source_channel.lstrip("@"))
    if query.grade:
        conditions.append(VacancyRow.detected_grade == query.grade)
    if query.work_format:
        conditions.append(VacancyRow.work_format == query.work_format)
    if query.date_from:
        dt_from = datetime.combine(query.date_from, time.min, tzinfo=timezone.utc)
        conditions.append(VacancyRow.post_date >= dt_from)
    if query.date_to:
        dt_to = datetime.combine(query.date_to, time.max, tzinfo=timezone.utc)
        conditions.append(VacancyRow.post_date <= dt_to)
    if query.q:
        conditions.append(VacancyRow.cleaned_text.ilike(f"%{query.q}%"))
    return stmt.where(and_(*conditions))


def vacancy_count_stmt(query: VacancyQuery) -> Select:
    from sqlalchemy import func, select

    base = select(func.count(VacancyRow.id))
    return apply_vacancy_filters(base, query)
