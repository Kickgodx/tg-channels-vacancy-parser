"""Агрегация метрик по вакансиям в БД (без загрузки всех строк в память)."""

from __future__ import annotations

from sqlalchemy import func, lateral, literal_column, select, true, tuple_
from sqlalchemy.orm import Session

from backend.db import fx_repository as fx_repo
from backend.db.models import VacancyRow
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.schemas import VacancyQuery
from backend.settings import get_settings
from jobdesk.config.loader import load_config
from jobdesk.models.metrics import (
    MetricsSummary,
    SalaryDisplayMeta,
    SalaryStats,
    TimeSeriesBucket,
)

_TOP_SPECS = 50
_TOP_TECHNOLOGIES = 50
_TOP_SALARY_SPECS = 25
_MAX_DAILY_BUCKETS = 400


def _salary_rub_expr(column=VacancyRow):
    """Колонка ``detected_salary_avg_rub`` (денормализация при upsert / FX backfill).

    Без runtime-join к ``exchange_rates``: correlated subquery раздувал planner cost
    и включал Postgres LLVM JIT (~1s) на каждом hit metrics/analytics.
    """
    return column.detected_salary_avg_rub


def _salary_rub_expr_table(table):
    """То же для Core-алиаса ``vacancies AS v``."""
    return table.c.detected_salary_avg_rub


def _prepare_fx(session: Session) -> SalaryDisplayMeta:
    """Метаданные курсов для UI: только последний снимок из БД, без HTTP к ЦБ.

    Синхронизация курсов — при старте API (`fx.sync_on_startup`) и явных FX-эндпоинтах.
    Агрегаты зарплат читают уже посчитанный ``detected_salary_avg_rub``.
    """
    cfg = load_config(get_settings().config_path)
    rate_date = None
    if cfg.fx.enabled:
        rate_date, _ = fx_repo.get_latest_rates(session)
    # Без курсов — None: сегодняшняя дата выдавала бы отсутствие курсов за свежие курсы.
    rates_as_of = rate_date.isoformat() if rate_date is not None else None
    return SalaryDisplayMeta(
        display_currency=cfg.fx.display_currency,
        rates_as_of=rates_as_of,
        rates_source=cfg.fx.provider,
    )


def _daily_buckets(session: Session, query: VacancyQuery) -> list[TimeSeriesBucket]:
    day = func.date(VacancyRow.post_date)
    stmt = apply_vacancy_filters(
        select(day, func.count(VacancyRow.id))
        .where(VacancyRow.post_date.isnot(None))
        .group_by(day)
        .order_by(day.desc()),
        query,
    ).limit(_MAX_DAILY_BUCKETS)
    # Лимит оставляет самые свежие дни; наружу — по возрастанию даты.
    rows = session.execute(stmt).all()
    return [TimeSeriesBucket(period=str(d), count=int(c)) for d, c in reversed(rows) if d is not None]


def _vacancies_alias():
    """Алиас обязателен: без correlate() LATERAL получает лишний FROM vacancies."""
    return VacancyRow.__table__.alias("v")


def _spec_lateral(v):
    """LATERAL unnest специализаций; v — алиас vacancies с .correlate(v)."""
    spec_q = select(func.jsonb_array_elements_text(v.c.detected_specializations).label("spec")).correlate(v)
    return lateral(spec_q).alias("spec_el")


def _grade_and_format_stats(session: Session, query: VacancyQuery):
    """Один проход: всего, с зарплатой, по грейду (число и зарплата) и по формату.

    ``GROUPING SETS ((грейд), (формат), ())`` вместо пяти отдельных запросов;
    ``grouping(col) = 1`` — колонка не входит в набор строки.
    """
    sal = _salary_rub_expr()
    grade = VacancyRow.detected_grade
    wf = func.coalesce(VacancyRow.work_format, literal_column("'unknown'"))
    stmt = apply_vacancy_filters(
        select(
            func.grouping(grade),
            func.grouping(wf),
            grade,
            wf,
            func.count(VacancyRow.id),
            func.count(sal),
            func.avg(sal),
        ).group_by(func.grouping_sets(tuple_(grade), tuple_(wf), tuple_())),
        query,
    )
    total = with_salary = 0
    by_grade: dict[str, int] = {}
    by_format: dict[str, int] = {}
    salary_by_grade: dict[str, SalaryStats] = {}
    for g_grade, g_wf, grade_v, wf_v, n, n_sal, avg_sal in session.execute(stmt).all():
        if g_grade and g_wf:
            total, with_salary = int(n), int(n_sal)
        elif not g_grade:
            if grade_v is None:
                continue
            by_grade[str(grade_v)] = int(n)
            if grade_v and n_sal and avg_sal is not None:
                salary_by_grade[str(grade_v)] = SalaryStats(mean=float(avg_sal), count_with_salary=int(n_sal))
        elif wf_v is not None:
            by_format[str(wf_v)] = int(n)
    return total, with_salary, by_grade, by_format, salary_by_grade


def _spec_stats(session: Session, query: VacancyQuery) -> tuple[dict[str, int], dict[str, SalaryStats]]:
    """Специализации одним запросом: число вакансий (топ-50) и зарплата (топ-25 по числу с зарплатой)."""
    v = _vacancies_alias()
    spec_lat = _spec_lateral(v)
    sal = _salary_rub_expr_table(v)
    stmt = apply_vacancy_filters(
        select(spec_lat.c.spec, func.count(v.c.id), func.count(sal), func.avg(sal))
        .select_from(v.join(spec_lat, true()))
        .group_by(spec_lat.c.spec),
        query,
        table=v,
    )
    rows = [r for r in session.execute(stmt).all() if r[0]]
    # При равном числе — по названию: граница топа не зависит от порядка строк из Postgres.
    by_count = sorted(rows, key=lambda r: (-r[1], r[0]))[:_TOP_SPECS]
    with_salary = (r for r in rows if r[2] and r[3] is not None)
    by_salary = sorted(with_salary, key=lambda r: (-r[2], r[0]))[:_TOP_SALARY_SPECS]
    counts = {str(spec): int(n) for spec, n, _, _ in by_count}
    salary = {
        str(spec): SalaryStats(mean=float(avg_sal), count_with_salary=int(n_sal))
        for spec, _, n_sal, avg_sal in by_salary
    }
    return counts, salary


def _tech_lateral(v):
    """LATERAL unnest технологий; v — алиас vacancies с .correlate(v)."""
    tech_q = select(func.jsonb_array_elements_text(v.c.technologies).label("tech")).correlate(v)
    return lateral(tech_q).alias("tech_el")


def technology_counts(session: Session, query: VacancyQuery, *, limit: int) -> dict[str, int]:
    """Технологии по числу вакансий под фильтром (топ ``limit``; при равенстве — по имени)."""
    v = _vacancies_alias()
    tech_lat = _tech_lateral(v)
    n = func.count(v.c.id)
    stmt = apply_vacancy_filters(
        select(tech_lat.c.tech, n)
        .select_from(v.join(tech_lat, true()))
        .group_by(tech_lat.c.tech)
        .order_by(n.desc(), tech_lat.c.tech)
        .limit(limit),
        query,
        table=v,
    )
    return {str(tech): int(cnt) for tech, cnt in session.execute(stmt).all() if tech}


def aggregate_vacancy_metrics(session: Session, query: VacancyQuery) -> MetricsSummary:
    """Сводка для графиков; те же фильтры, что у GET /vacancies.

    Пять запросов: курс, грейды/форматы/итоги, специализации, технологии, дни.
    """
    salary_display = _prepare_fx(session)
    total, with_salary, by_grade, by_format, salary_by_grade = _grade_and_format_stats(session, query)
    if total == 0:
        return MetricsSummary(unique_vacancies=0, total_vacancies=0, salary_display=salary_display)

    by_spec, salary_spec = _spec_stats(session, query)
    return MetricsSummary(
        unique_vacancies=total,
        total_vacancies=total,
        duplicate_count=0,
        vacancies_by_specialization=by_spec,
        vacancies_by_grade=by_grade,
        vacancies_by_work_format=by_format,
        daily=_daily_buckets(session, query),
        vacancies_with_salary=with_salary,
        vacancies_without_salary=total - with_salary,
        salary_by_specialization=salary_spec,
        salary_by_grade=salary_by_grade,
        grade_distribution=by_grade,
        technology_top=technology_counts(session, query, limit=_TOP_TECHNOLOGIES),
        salary_display=salary_display,
    )
