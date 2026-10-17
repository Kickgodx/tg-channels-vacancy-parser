"""Общие фильтры списка вакансий и агрегатов метрик."""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import (
    ColumnElement,
    Table,
    and_,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.sql import Select

from backend.db.models import VacancyRow, VacancySightingRow
from backend.schemas import VacancyQuery


def _specializations_match(c, specs: list[str]) -> ColumnElement[bool]:
    """Хотя бы одна из выбранных специализаций в JSONB-массиве вакансии (``@>`` идёт по GIN)."""
    return or_(*[c.detected_specializations.contains([spec]) for spec in specs])


def fts_query(column, q: str):
    """``to_tsvector`` / ``websearch_to_tsquery`` по русской конфигурации — для WHERE и ``ts_rank``."""
    return (
        func.to_tsvector(literal_column("'russian'"), column),
        func.websearch_to_tsquery(literal_column("'russian'"), q),
    )


def vacancy_filter_conditions(
    query: VacancyQuery,
    *,
    table: Table | None = None,
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
        conditions.append(_specializations_match(c, query.specializations))
    if query.work_format:
        conditions.append(c.work_format == query.work_format)
    if query.date_from:
        dt_from = datetime.combine(query.date_from, time.min, tzinfo=UTC)
        conditions.append(c.post_date >= dt_from)
    if query.date_to:
        dt_to = datetime.combine(query.date_to, time.max, tzinfo=UTC)
        conditions.append(c.post_date <= dt_to)
    if query.q:
        document, ts_query = fts_query(c.cleaned_text, query.q)
        conditions.append(document.op("@@")(ts_query))
    if query.has_salary:
        conditions.append(
            or_(
                c.detected_salary_min.isnot(None),
                c.detected_salary_max.isnot(None),
                c.detected_salary_avg.isnot(None),
            )
        )
    return conditions


def sighting_filter_conditions(query: VacancyQuery) -> list[ColumnElement[bool]]:
    """Условия для ``vacancy_sightings JOIN vacancies``: появления постов под фильтром.

    Канал и даты — у самого поста: дубль из канала B ссылается на вакансию канала A,
    а ``post_date`` вакансии сдвигается вперёд перепостами. Остальное — у канонической
    вакансии, теми же условиями, что список и аналитика.
    """
    s = VacancySightingRow
    own = query.model_copy(update={"source_channel": None, "date_from": None, "date_to": None})
    conditions = vacancy_filter_conditions(own)
    if query.source_channel:
        conditions.append(s.source_channel == query.source_channel.lstrip("@"))
    if query.date_from:
        conditions.append(s.post_date >= datetime.combine(query.date_from, time.min, tzinfo=UTC))
    if query.date_to:
        conditions.append(s.post_date <= datetime.combine(query.date_to, time.max, tzinfo=UTC))
    return conditions


def apply_vacancy_filters(
    stmt: Select,
    query: VacancyQuery,
    *,
    table: Table | None = None,
    include_duplicates: bool = False,
) -> Select:
    """Ограничить выборку уникальными вакансиями и параметрами запроса."""
    conditions = vacancy_filter_conditions(query, table=table, include_duplicates=include_duplicates)
    if not conditions:
        return stmt
    return stmt.where(and_(*conditions))


def vacancy_count_stmt(query: VacancyQuery) -> Select:
    return apply_vacancy_filters(select(func.count(VacancyRow.id)), query)
