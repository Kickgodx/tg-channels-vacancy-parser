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

from backend.db.models import VacancyRow, VacancySalaryEstimateRow, VacancySightingRow
from backend.schemas import VacancyQuery
from tg_jobs_parser.config.schema import canonical_specializations, canonical_technologies


def _specializations_match(c, specs: list[str]) -> ColumnElement[bool]:
    """Хотя бы одна из выбранных специализаций в JSONB-массиве вакансии (``@>`` идёт по GIN).

    Старые имена (``AQA``, ``Manual QA`` в закладках и ссылках) приводятся к каноническим.
    """
    return or_(*[c.detected_specializations.contains([spec]) for spec in canonical_specializations(specs)])


def _technologies_match(c, techs: list[str]) -> ColumnElement[bool]:
    """Хотя бы одна из выбранных технологий в JSONB-массиве вакансии (``@>`` по GIN, миграция 037).

    Имена в БД канонические (``canonical_technologies`` при записи и в миграции), поэтому
    запрос приводится к тому же написанию: ``golang`` → ``Go``.
    """
    return or_(*[c.technologies.contains([tech]) for tech in canonical_technologies(techs)])


def fts_query(document, q: str):
    """Документ (хранимый ``search_tsv``) и ``websearch_to_tsquery`` русской конфигурации — для WHERE и ``ts_rank``."""
    return document, func.websearch_to_tsquery(literal_column("'russian'"), q)


def _semantic_ids(query: VacancyQuery, *, include_duplicates: bool):
    """Окно смыслового поиска: ближайшие к запросу вакансии под остальными фильтрами."""
    from backend.db.embedding_queries import semantic_window_ids

    v = VacancyRow.__table__.alias("semantic_window")
    rest = query.model_copy(update={"q": None, "semantic_vector": None})
    return semantic_window_ids(
        v,
        query.semantic_vector,
        vacancy_filter_conditions(rest, table=v, include_duplicates=include_duplicates),
        model=query.embedding_model or "",
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
    if query.technologies:
        # Через И со специализациями: «Automation QA + Python» — AQA на Python.
        conditions.append(_technologies_match(c, query.technologies))
    if query.work_format:
        conditions.append(c.work_format == query.work_format)
    if query.date_from:
        dt_from = datetime.combine(query.date_from, time.min, tzinfo=UTC)
        conditions.append(c.post_date >= dt_from)
    if query.date_to:
        dt_to = datetime.combine(query.date_to, time.max, tzinfo=UTC)
        conditions.append(c.post_date <= dt_to)
    if query.q and query.search_mode == "semantic" and query.semantic_vector is not None:
        conditions.append(c.id.in_(_semantic_ids(query, include_duplicates=include_duplicates)))
    elif query.q:
        document, ts_query = fts_query(c.search_tsv, query.q)
        conditions.append(document.op("@@")(ts_query))
    # «С зарплатой» — есть рублёвая сумма, как в метриках и аналитике: иначе список
    # и цифры над ним расходятся (зарплата в валюте без курса суммы не получает).
    if query.has_salary is not None:
        has_salary = c.detected_salary_avg_rub.isnot(None)
        if query.salary_estimates:
            # Оценка по похожим — только для списка: аналитика сбрасывает флаг (analytics_query).
            has_salary = or_(has_salary, _has_estimate(c))
        conditions.append(has_salary if query.has_salary else ~has_salary)
    return conditions


def _has_estimate(c) -> ColumnElement[bool]:
    e = VacancySalaryEstimateRow
    return select(e.vacancy_id).where(e.vacancy_id == c.id, e.median.isnot(None)).exists()


def analytics_query(query: VacancyQuery) -> VacancyQuery:
    """Запрос агрегатов: оценки зарплаты в аналитику не входят — только указанные зарплаты."""
    return query.model_copy(update={"salary_estimates": False}) if query.salary_estimates else query


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
