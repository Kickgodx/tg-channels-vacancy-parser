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


def _salary_by_grade(session: Session, query: VacancyQuery, *, dialect: str) -> dict[str, SalaryStats]:
    sal = _salary_expr()
    stmt = apply_vacancy_filters(
        select(
            VacancyRow.detected_grade,
            func.avg(sal),
            func.count(VacancyRow.id),
        )
        .where(sal.isnot(None))
        .group_by(VacancyRow.detected_grade),
        query,
        dialect=dialect,
    )
    out: dict[str, SalaryStats] = {}
    for grade, avg_val, cnt in session.execute(stmt).all():
        if grade and cnt and avg_val is not None:
            out[str(grade)] = SalaryStats(mean=float(avg_val), count_with_salary=int(cnt))
    return out


def _count_by_column(
    session: Session,
    query: VacancyQuery,
    column,
    *,
    dialect: str,
    null_label: str | None = None,
) -> dict[str, int]:
    label = func.coalesce(column, null_label) if null_label else column
    stmt = apply_vacancy_filters(
        select(label, func.count(VacancyRow.id)).group_by(label),
        query,
        dialect=dialect,
    )
    rows = session.execute(stmt).all()
    return {str(k): int(c) for k, c in rows if k is not None}


def _daily_buckets(session: Session, query: VacancyQuery, *, dialect: str) -> list[TimeSeriesBucket]:
    day = func.date(VacancyRow.post_date)
    stmt = (
        apply_vacancy_filters(
            select(day, func.count(VacancyRow.id))
            .where(VacancyRow.post_date.isnot(None))
            .group_by(day)
            .order_by(day),
            query,
            dialect=dialect,
        )
        .limit(_MAX_DAILY_BUCKETS)
    )
    return [TimeSeriesBucket(period=str(d), count=int(c)) for d, c in session.execute(stmt).all() if d is not None]


def _vacancies_alias():
    """Алиас обязателен: без correlate() LATERAL получает лишний FROM vacancies."""
    return VacancyRow.__table__.alias("v")


def _salary_expr_table(table):
    return func.coalesce(
        table.c.detected_salary_avg,
        (table.c.detected_salary_min + table.c.detected_salary_max) / 2.0,
        table.c.detected_salary_min,
        table.c.detected_salary_max,
    )


def _pg_spec_lateral(v):
    """LATERAL unnest специализаций; v — алиас vacancies с .correlate(v)."""
    from sqlalchemy.dialects.postgresql import JSONB

    spec_q = select(
        func.jsonb_array_elements_text(cast(v.c.detected_specializations, JSONB)).label("spec")
    ).correlate(v)
    return lateral(spec_q).alias("spec_el")


def _spec_counts_postgresql(session: Session, query: VacancyQuery, *, dialect: str) -> dict[str, int]:
    v = _vacancies_alias()
    spec_lat = _pg_spec_lateral(v)
    stmt = apply_vacancy_filters(
        select(spec_lat.c.spec, func.count(v.c.id))
        .select_from(v.join(spec_lat, true()))
        .group_by(spec_lat.c.spec)
        .order_by(func.count(v.c.id).desc())
        .limit(_TOP_SPECS),
        query,
        table=v,
        dialect=dialect,
    )
    return {str(s): int(c) for s, c in session.execute(stmt).all() if s}


def _spec_counts_sqlite(session: Session, query: VacancyQuery, *, dialect: str) -> dict[str, int]:
    je = func.json_each(VacancyRow.detected_specializations).table_valued("value", with_ordinality=False)
    stmt = apply_vacancy_filters(
        select(je.c.value, func.count(VacancyRow.id))
        .select_from(VacancyRow)
        .join(je, true())
        .group_by(je.c.value)
        .order_by(func.count(VacancyRow.id).desc())
        .limit(_TOP_SPECS),
        query,
        dialect=dialect,
    )
    return {str(s): int(c) for s, c in session.execute(stmt).all() if s}


def _salary_by_spec_postgresql(session: Session, query: VacancyQuery, *, dialect: str) -> dict[str, SalaryStats]:
    v = _vacancies_alias()
    spec_lat = _pg_spec_lateral(v)
    sal = _salary_expr_table(v)
    stmt = apply_vacancy_filters(
        select(
            spec_lat.c.spec,
            func.avg(sal),
            func.count(v.c.id),
        )
        .select_from(v.join(spec_lat, true()))
        .where(sal.isnot(None))
        .group_by(spec_lat.c.spec)
        .order_by(func.count(v.c.id).desc())
        .limit(_TOP_SALARY_SPECS),
        query,
        table=v,
        dialect=dialect,
    )
    out: dict[str, SalaryStats] = {}
    for s, avg_val, cnt in session.execute(stmt).all():
        if s and cnt and avg_val is not None:
            out[str(s)] = SalaryStats(mean=float(avg_val), count_with_salary=int(cnt))
    return out


def _salary_by_spec_sqlite(session: Session, query: VacancyQuery, *, dialect: str) -> dict[str, SalaryStats]:
    je = func.json_each(VacancyRow.detected_specializations).table_valued("value", with_ordinality=False)
    sal = _salary_expr()
    stmt = apply_vacancy_filters(
        select(je.c.value, func.avg(sal), func.count(VacancyRow.id))
        .select_from(VacancyRow)
        .join(je, true())
        .where(sal.isnot(None))
        .group_by(je.c.value)
        .order_by(func.count(VacancyRow.id).desc())
        .limit(_TOP_SALARY_SPECS),
        query,
        dialect=dialect,
    )
    out: dict[str, SalaryStats] = {}
    for s, avg_val, cnt in session.execute(stmt).all():
        if s and cnt and avg_val is not None:
            out[str(s)] = SalaryStats(mean=float(avg_val), count_with_salary=int(cnt))
    return out


def aggregate_vacancy_metrics(session: Session, query: VacancyQuery) -> MetricsSummary:
    """Сводка для графиков; те же фильтры, что у GET /vacancies."""
    dialect = _dialect_name(session)
    total = int(session.scalar(vacancy_count_stmt(query, dialect=dialect)) or 0)
    if total == 0:
        return MetricsSummary(unique_vacancies=0, total_vacancies=0)

    by_spec = (
        _spec_counts_postgresql(session, query, dialect=dialect)
        if dialect == "postgresql"
        else _spec_counts_sqlite(session, query, dialect=dialect)
    )
    salary_spec = (
        _salary_by_spec_postgresql(session, query, dialect=dialect)
        if dialect == "postgresql"
        else _salary_by_spec_sqlite(session, query, dialect=dialect)
    )

    sal_expr = _salary_expr()
    with_salary = int(
        session.scalar(
            apply_vacancy_filters(
                select(func.count(VacancyRow.id)).where(sal_expr.isnot(None)),
                query,
                dialect=dialect,
            )
        )
        or 0
    )

    return MetricsSummary(
        unique_vacancies=total,
        total_vacancies=total,
        duplicate_count=0,
        vacancies_by_specialization=by_spec,
        vacancies_by_grade=_count_by_column(
            session, query, VacancyRow.detected_grade, dialect=dialect
        ),
        vacancies_by_work_format=_count_by_column(
            session, query, VacancyRow.work_format, null_label="unknown", dialect=dialect
        ),
        daily=_daily_buckets(session, query, dialect=dialect),
        vacancies_with_salary=with_salary,
        vacancies_without_salary=total - with_salary,
        salary_by_specialization=salary_spec,
        salary_by_grade=_salary_by_grade(session, query, dialect=dialect),
        grade_distribution=_count_by_column(
            session, query, VacancyRow.detected_grade, dialect=dialect
        ),
    )
