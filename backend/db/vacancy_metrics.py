"""Агрегация метрик по вакансиям в БД (без загрузки всех строк в память)."""

from __future__ import annotations

from sqlalchemy import cast, func, lateral, select, true
from sqlalchemy.orm import Session

from backend.db.models import VacancyRow
from backend.db.vacancy_filters import apply_vacancy_filters, vacancy_count_stmt
from backend.schemas import VacancyQuery
from tg_jobs_parser.models.metrics import MetricsSummary, SalaryStats, TimeSeriesBucket

_TOP_SPECS = 50
_TOP_SALARY_SPECS = 25
_MAX_DAILY_BUCKETS = 400


def _dialect_name(session: Session) -> str:
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else "postgresql"


def _salary_expr():
    """Средняя зарплата для агрегатов (как в metrics_builder, упрощённо)."""
    return func.coalesce(
        VacancyRow.detected_salary_avg,
        (VacancyRow.detected_salary_min + VacancyRow.detected_salary_max) / 2.0,
        VacancyRow.detected_salary_min,
        VacancyRow.detected_salary_max,
    )


def _count_by_column(session: Session, query: VacancyQuery, column, *, null_label: str | None = None) -> dict[str, int]:
    label = func.coalesce(column, null_label) if null_label else column
    stmt = apply_vacancy_filters(
        select(label, func.count(VacancyRow.id)).group_by(label),
        query,
    )
    rows = session.execute(stmt).all()
    return {str(k): int(c) for k, c in rows if k is not None}


def _daily_buckets(session: Session, query: VacancyQuery) -> list[TimeSeriesBucket]:
    day = func.date(VacancyRow.post_date)
    stmt = (
        apply_vacancy_filters(
            select(day, func.count(VacancyRow.id))
            .where(VacancyRow.post_date.isnot(None))
            .group_by(day)
            .order_by(day),
            query,
        )
        .limit(_MAX_DAILY_BUCKETS)
    )
    return [TimeSeriesBucket(period=str(d), count=int(c)) for d, c in session.execute(stmt).all() if d is not None]


def _vacancies_table():
    return VacancyRow.__table__


def _salary_expr_table(table):
    return func.coalesce(
        table.c.detected_salary_avg,
        (table.c.detected_salary_min + table.c.detected_salary_max) / 2.0,
        table.c.detected_salary_min,
        table.c.detected_salary_max,
    )


def _pg_spec_lateral(table):
    """LATERAL unnest специализаций (Core SQL — ORM join к lateral alias ломается)."""
    from sqlalchemy.dialects.postgresql import JSONB

    return lateral(
        select(
            func.jsonb_array_elements_text(cast(table.c.detected_specializations, JSONB)).label("spec")
        )
    ).alias("spec_el")


def _spec_counts_postgresql(session: Session, query: VacancyQuery) -> dict[str, int]:
    t = _vacancies_table()
    spec_lat = _pg_spec_lateral(t)
    stmt = apply_vacancy_filters(
        select(spec_lat.c.spec, func.count(t.c.id))
        .select_from(t.join(spec_lat, true()))
        .group_by(spec_lat.c.spec)
        .order_by(func.count(t.c.id).desc())
        .limit(_TOP_SPECS),
        query,
    )
    return {str(s): int(c) for s, c in session.execute(stmt).all() if s}


def _spec_counts_sqlite(session: Session, query: VacancyQuery) -> dict[str, int]:
    je = func.json_each(VacancyRow.detected_specializations).table_valued("value", with_ordinality=False)
    stmt = apply_vacancy_filters(
        select(je.c.value, func.count(VacancyRow.id))
        .select_from(VacancyRow, je)
        .group_by(je.c.value)
        .order_by(func.count(VacancyRow.id).desc())
        .limit(_TOP_SPECS),
        query,
    )
    return {str(s): int(c) for s, c in session.execute(stmt).all() if s}


def _salary_by_spec_postgresql(session: Session, query: VacancyQuery) -> dict[str, SalaryStats]:
    t = _vacancies_table()
    spec_lat = _pg_spec_lateral(t)
    sal = _salary_expr_table(t)
    stmt = apply_vacancy_filters(
        select(
            spec_lat.c.spec,
            func.avg(sal),
            func.count(t.c.id),
        )
        .select_from(t.join(spec_lat, true()))
        .where(sal.isnot(None))
        .group_by(spec_lat.c.spec)
        .order_by(func.count(t.c.id).desc())
        .limit(_TOP_SALARY_SPECS),
        query,
    )
    out: dict[str, SalaryStats] = {}
    for s, avg_val, cnt in session.execute(stmt).all():
        if s and cnt and avg_val is not None:
            out[str(s)] = SalaryStats(mean=float(avg_val), count_with_salary=int(cnt))
    return out


def _salary_by_spec_sqlite(session: Session, query: VacancyQuery) -> dict[str, SalaryStats]:
    je = func.json_each(VacancyRow.detected_specializations).table_valued("value", with_ordinality=False)
    sal = _salary_expr()
    stmt = apply_vacancy_filters(
        select(je.c.value, func.avg(sal), func.count(VacancyRow.id))
        .select_from(VacancyRow, je)
        .where(sal.isnot(None))
        .group_by(je.c.value)
        .order_by(func.count(VacancyRow.id).desc())
        .limit(_TOP_SALARY_SPECS),
        query,
    )
    out: dict[str, SalaryStats] = {}
    for s, avg_val, cnt in session.execute(stmt).all():
        if s and cnt and avg_val is not None:
            out[str(s)] = SalaryStats(mean=float(avg_val), count_with_salary=int(cnt))
    return out


def aggregate_vacancy_metrics(session: Session, query: VacancyQuery) -> MetricsSummary:
    """Сводка для графиков; те же фильтры, что у GET /vacancies."""
    total = int(session.scalar(vacancy_count_stmt(query)) or 0)
    if total == 0:
        return MetricsSummary(unique_vacancies=0, total_vacancies=0)

    dialect = _dialect_name(session)
    by_spec = (
        _spec_counts_postgresql(session, query)
        if dialect == "postgresql"
        else _spec_counts_sqlite(session, query)
    )
    salary_spec = (
        _salary_by_spec_postgresql(session, query)
        if dialect == "postgresql"
        else _salary_by_spec_sqlite(session, query)
    )

    sal_expr = _salary_expr()
    with_salary = int(
        session.scalar(
            apply_vacancy_filters(
                select(func.count(VacancyRow.id)).where(sal_expr.isnot(None)),
                query,
            )
        )
        or 0
    )

    return MetricsSummary(
        unique_vacancies=total,
        total_vacancies=total,
        duplicate_count=0,
        vacancies_by_specialization=by_spec,
        vacancies_by_grade=_count_by_column(session, query, VacancyRow.detected_grade),
        vacancies_by_work_format=_count_by_column(
            session, query, VacancyRow.work_format, null_label="unknown"
        ),
        daily=_daily_buckets(session, query),
        vacancies_with_salary=with_salary,
        vacancies_without_salary=total - with_salary,
        salary_by_specialization=salary_spec,
        grade_distribution=_count_by_column(session, query, VacancyRow.detected_grade),
    )
