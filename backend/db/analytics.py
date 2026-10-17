"""Аналитика дашборда: зарплатные квантили, качество источников, топ компаний."""

from __future__ import annotations

import math
from collections import defaultdict

from sqlalchemy import case, func, literal, select, true
from sqlalchemy.orm import Session

from backend.db.models import VacancyRow, VacancySightingRow
from backend.db.vacancy_filters import apply_vacancy_filters, sighting_filter_conditions
from backend.db.vacancy_metrics import (
    _dialect_name,
    _pg_spec_lateral,
    _prepare_fx,
    _salary_rub_expr,
    _salary_rub_expr_table,
    _vacancies_alias,
)
from backend.schemas import (
    CompanyStats,
    SalaryAnalytics,
    SalaryDistributionBucket,
    SalaryGroupStats,
    SalaryMonthlyBucket,
    SalaryPeriodGroupBucket,
    SourceStats,
    VacancyQuery,
)

PeriodGrain = str  # "year" | "month"

_TOP_COMPANIES = 20
_TOP_SALARY_SPECS = 15

_SALARY_BANDS: list[tuple[float | None, str]] = [
    (100_000.0, "до 100k"),
    (150_000.0, "100–150k"),
    (200_000.0, "150–200k"),
    (250_000.0, "200–250k"),
    (300_000.0, "250–300k"),
    (400_000.0, "300–400k"),
    (None, "400k+"),
]


def _quantile(sorted_values: list[float], q: float) -> float | None:
    """Линейная интерполяция, как percentile_cont в PostgreSQL."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _stats_from_values(group: str, values: list[float]) -> SalaryGroupStats:
    s = sorted(values)
    return SalaryGroupStats(
        group=group,
        count=len(s),
        mean=sum(s) / len(s),
        min=s[0],
        q1=_quantile(s, 0.25),
        median=_quantile(s, 0.5),
        q3=_quantile(s, 0.75),
        max=s[-1],
    )


def _group_stats_pg(
    session: Session,
    query: VacancyQuery,
    group_expr,
    *,
    dialect: str,
    table=None,
    from_clause=None,
) -> list[SalaryGroupStats]:
    """Квантили через percentile_cont WITHIN GROUP (PostgreSQL)."""
    sal = _salary_rub_expr_table(table) if table is not None else _salary_rub_expr()
    stmt = select(
        group_expr.label("grp"),
        func.count().label("n"),
        func.avg(sal),
        func.min(sal),
        func.percentile_cont(0.25).within_group(sal),
        func.percentile_cont(0.5).within_group(sal),
        func.percentile_cont(0.75).within_group(sal),
        func.max(sal),
    ).where(sal.isnot(None))
    if from_clause is not None:
        stmt = stmt.select_from(from_clause)
    stmt = apply_vacancy_filters(
        stmt.group_by(group_expr),
        query,
        table=table,
        dialect=dialect,
    )
    out: list[SalaryGroupStats] = []
    for grp, n, mean_v, min_v, q1, med, q3, max_v in session.execute(stmt).all():
        if grp is None or not n:
            continue
        out.append(
            SalaryGroupStats(
                group=str(grp),
                count=int(n),
                mean=float(mean_v) if mean_v is not None else None,
                min=float(min_v) if min_v is not None else None,
                q1=float(q1) if q1 is not None else None,
                median=float(med) if med is not None else None,
                q3=float(q3) if q3 is not None else None,
                max=float(max_v) if max_v is not None else None,
            )
        )
    return out


def _group_stats_generic(
    session: Session,
    query: VacancyQuery,
    group_expr,
    *,
    dialect: str,
    table=None,
    from_clause=None,
) -> list[SalaryGroupStats]:
    """Квантили в Python — для SQLite (тесты) и прочих диалектов."""
    sal = _salary_rub_expr_table(table) if table is not None else _salary_rub_expr()
    stmt = select(group_expr.label("grp"), sal.label("sal")).where(sal.isnot(None))
    if from_clause is not None:
        stmt = stmt.select_from(from_clause)
    stmt = apply_vacancy_filters(stmt, query, table=table, dialect=dialect)
    values: dict[str, list[float]] = defaultdict(list)
    for grp, v in session.execute(stmt).all():
        if grp is not None and v is not None:
            values[str(grp)].append(float(v))
    return [_stats_from_values(g, vals) for g, vals in values.items()]


def _group_stats(
    session: Session,
    query: VacancyQuery,
    group_expr,
    *,
    dialect: str,
    table=None,
    from_clause=None,
) -> list[SalaryGroupStats]:
    fn = _group_stats_pg if dialect == "postgresql" else _group_stats_generic
    rows = fn(
        session,
        query,
        group_expr,
        dialect=dialect,
        table=table,
        from_clause=from_clause,
    )
    rows.sort(key=lambda r: (-r.count, r.group))
    return rows


def _salary_by_spec(session: Session, query: VacancyQuery, *, dialect: str) -> list[SalaryGroupStats]:
    if dialect == "postgresql":
        v = _vacancies_alias()
        spec_lat = _pg_spec_lateral(v)
        rows = _group_stats_pg(
            session,
            query,
            spec_lat.c.spec,
            dialect=dialect,
            table=v,
            from_clause=v.join(spec_lat, true()),
        )
    else:
        je = func.json_each(VacancyRow.detected_specializations).table_valued("value", with_ordinality=False)
        rows = _group_stats_generic(
            session,
            query,
            je.c.value,
            dialect=dialect,
            from_clause=VacancyRow.__table__.join(je, true()),
        )
    rows.sort(key=lambda r: (-r.count, r.group))
    return rows[:_TOP_SALARY_SPECS]


def _period_key(post_date, grain: PeriodGrain) -> str:
    return post_date.strftime("%Y") if grain == "year" else post_date.strftime("%Y-%m")


def _salary_period_buckets(
    session: Session,
    query: VacancyQuery,
    *,
    dialect: str,
    grain: PeriodGrain,
) -> list[SalaryMonthlyBucket]:
    sal = _salary_rub_expr()
    if dialect == "postgresql":
        trunc = func.date_trunc(grain, VacancyRow.post_date)
        stmt = apply_vacancy_filters(
            select(
                trunc.label("p"),
                func.count(),
                func.avg(sal),
                func.percentile_cont(0.5).within_group(sal),
            )
            .where(VacancyRow.post_date.isnot(None), sal.isnot(None))
            .group_by(trunc)
            .order_by(trunc),
            query,
            dialect=dialect,
        )
        fmt = "%Y" if grain == "year" else "%Y-%m"
        return [
            SalaryMonthlyBucket(
                period=p.strftime(fmt),
                count=int(n),
                mean=float(mn) if mn is not None else None,
                median=float(md) if md is not None else None,
            )
            for p, n, mn, md in session.execute(stmt).all()
            if p is not None
        ]
    stmt = apply_vacancy_filters(
        select(VacancyRow.post_date, sal.label("sal")).where(VacancyRow.post_date.isnot(None), sal.isnot(None)),
        query,
        dialect=dialect,
    )
    values: dict[str, list[float]] = defaultdict(list)
    for post_date, v in session.execute(stmt).all():
        if post_date is not None and v is not None:
            values[_period_key(post_date, grain)].append(float(v))
    out: list[SalaryMonthlyBucket] = []
    for period in sorted(values):
        vals = sorted(values[period])
        out.append(
            SalaryMonthlyBucket(
                period=period,
                count=len(vals),
                mean=sum(vals) / len(vals),
                median=_quantile(vals, 0.5),
            )
        )
    return out


def _period_group_bucket(period: str, group: str, values: list[float]) -> SalaryPeriodGroupBucket:
    s = sorted(values)
    return SalaryPeriodGroupBucket(
        period=period,
        group=group,
        count=len(s),
        mean=sum(s) / len(s),
        min=s[0],
        q1=_quantile(s, 0.25),
        median=_quantile(s, 0.5),
        q3=_quantile(s, 0.75),
        max=s[-1],
    )


def _salary_period_group(
    session: Session,
    query: VacancyQuery,
    group_expr,
    *,
    dialect: str,
    grain: PeriodGrain,
) -> list[SalaryPeriodGroupBucket]:
    sal = _salary_rub_expr()
    if dialect == "postgresql":
        trunc = func.date_trunc(grain, VacancyRow.post_date)
        stmt = apply_vacancy_filters(
            select(
                trunc.label("p"),
                group_expr.label("grp"),
                func.count(),
                func.avg(sal),
                func.min(sal),
                func.percentile_cont(0.25).within_group(sal),
                func.percentile_cont(0.5).within_group(sal),
                func.percentile_cont(0.75).within_group(sal),
                func.max(sal),
            )
            .where(VacancyRow.post_date.isnot(None), sal.isnot(None))
            .group_by(trunc, group_expr)
            .order_by(trunc, group_expr),
            query,
            dialect=dialect,
        )
        fmt = "%Y" if grain == "year" else "%Y-%m"
        out: list[SalaryPeriodGroupBucket] = []
        for p, grp, n, mean_v, min_v, q1, med, q3, max_v in session.execute(stmt).all():
            if p is None or grp is None or not n:
                continue
            out.append(
                SalaryPeriodGroupBucket(
                    period=p.strftime(fmt),
                    group=str(grp),
                    count=int(n),
                    mean=float(mean_v) if mean_v is not None else None,
                    min=float(min_v) if min_v is not None else None,
                    q1=float(q1) if q1 is not None else None,
                    median=float(med) if med is not None else None,
                    q3=float(q3) if q3 is not None else None,
                    max=float(max_v) if max_v is not None else None,
                )
            )
        return out

    stmt = apply_vacancy_filters(
        select(VacancyRow.post_date, group_expr.label("grp"), sal.label("sal")).where(
            VacancyRow.post_date.isnot(None),
            sal.isnot(None),
        ),
        query,
        dialect=dialect,
    )
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for post_date, grp, v in session.execute(stmt).all():
        if post_date is None or grp is None or v is None:
            continue
        values[(_period_key(post_date, grain), str(grp))].append(float(v))
    out = [_period_group_bucket(period, group, vals) for (period, group), vals in values.items()]
    out.sort(key=lambda r: (r.period, -r.count, r.group))
    return out


def _salary_distribution(session: Session, query: VacancyQuery, *, dialect: str) -> list[SalaryDistributionBucket]:
    sal = _salary_rub_expr()
    band = case(
        *[(sal < edge, literal(label)) for edge, label in _SALARY_BANDS[:-1]],
        else_=literal(_SALARY_BANDS[-1][1]),
    )
    stmt = apply_vacancy_filters(
        select(band.label("band"), func.count()).where(sal.isnot(None)).group_by(band),
        query,
        dialect=dialect,
    )
    counts = {str(b): int(c) for b, c in session.execute(stmt).all()}
    return [SalaryDistributionBucket(range=label, count=counts.get(label, 0)) for _, label in _SALARY_BANDS]


def salary_analytics(session: Session, query: VacancyQuery) -> SalaryAnalytics:
    """Зарплатная аналитика по фильтрам списка вакансий."""
    dialect = _dialect_name(session)
    salary_display = _prepare_fx(session)
    wf_expr = func.coalesce(VacancyRow.work_format, literal("unknown"))
    grade_expr = VacancyRow.detected_grade
    return SalaryAnalytics(
        by_grade=_group_stats(session, query, grade_expr, dialect=dialect),
        by_work_format=_group_stats(session, query, wf_expr, dialect=dialect),
        by_specialization=_salary_by_spec(session, query, dialect=dialect),
        monthly=_salary_period_buckets(session, query, dialect=dialect, grain="month"),
        yearly=_salary_period_buckets(session, query, dialect=dialect, grain="year"),
        by_grade_yearly=_salary_period_group(session, query, grade_expr, dialect=dialect, grain="year"),
        by_work_format_yearly=_salary_period_group(session, query, wf_expr, dialect=dialect, grain="year"),
        by_grade_monthly=_salary_period_group(session, query, grade_expr, dialect=dialect, grain="month"),
        by_work_format_monthly=_salary_period_group(session, query, wf_expr, dialect=dialect, grain="month"),
        distribution=_salary_distribution(session, query, dialect=dialect),
        salary_display=salary_display,
    )


def source_quality(session: Session, query: VacancyQuery) -> list[SourceStats]:
    """По каждому каналу: уникальные вакансии, дубли, доля уникальных постов, score, зарплата.

    ``vacancies``, score и зарплата — по строкам ``vacancies`` канала. Дубли и
    ``unique_share`` для Telegram — по ``vacancy_sightings``: доля постов канала,
    чей текст до этого не встречался (ни в другом канале, ни у него самого).
    У HH появлений нет — там дубли это строки ``is_duplicate`` (мультигород).
    """
    dialect = _dialect_name(session)
    sal = _salary_rub_expr()
    stmt = apply_vacancy_filters(
        select(
            VacancyRow.source_channel,
            VacancyRow.is_duplicate,
            func.count(),
            func.avg(VacancyRow.vacancy_score),
            func.avg(case((sal.isnot(None), 1.0), else_=0.0)),
        ).group_by(VacancyRow.source_channel, VacancyRow.is_duplicate),
        query,
        dialect=dialect,
        include_duplicates=True,
    )
    per_channel: dict[str, dict] = {}

    def _channel(name: object) -> dict:
        return per_channel.setdefault(
            str(name),
            {"unique": 0, "dups": 0, "avg_score": None, "sal_share": 0.0, "posts": 0, "post_dups": 0},
        )

    for channel, is_dup, cnt, avg_score, sal_share in session.execute(stmt).all():
        d = _channel(channel)
        if is_dup:
            d["dups"] += int(cnt)
        else:
            d["unique"] += int(cnt)
            d["avg_score"] = float(avg_score) if avg_score is not None else None
            d["sal_share"] = float(sal_share or 0.0)

    s = VacancySightingRow
    sightings = (
        select(s.source_channel, func.count(), func.sum(case((s.is_duplicate, 1), else_=0)))
        .join(VacancyRow, VacancyRow.id == s.vacancy_id)
        .where(*sighting_filter_conditions(query, dialect=dialect))
        .group_by(s.source_channel)
    )
    # Канал может не иметь своих вакансий вовсе — только перепосты чужих: он тоже в отчёте.
    for channel, posts, post_dups in session.execute(sightings).all():
        d = _channel(channel)
        d["posts"] += int(posts)
        d["post_dups"] += int(post_dups or 0)

    out: list[SourceStats] = []
    for ch, d in per_channel.items():
        if d["posts"]:
            share = (d["posts"] - d["post_dups"]) / d["posts"]
        else:
            rows = d["unique"] + d["dups"]
            share = d["unique"] / rows if rows else 1.0
        out.append(
            SourceStats(
                channel=ch,
                vacancies=d["unique"],
                duplicates=d["dups"] + d["post_dups"],
                unique_share=share,
                avg_score=d["avg_score"],
                with_salary_share=d["sal_share"],
            )
        )
    out.sort(key=lambda s: (-s.vacancies, s.channel))
    return out


def top_companies(session: Session, query: VacancyQuery, *, limit: int = _TOP_COMPANIES) -> list[CompanyStats]:
    """Топ работодателей по числу вакансий."""
    dialect = _dialect_name(session)
    sal = _salary_rub_expr()
    stmt = apply_vacancy_filters(
        select(
            VacancyRow.company,
            func.count(),
            func.avg(VacancyRow.vacancy_score),
            func.count(case((sal.isnot(None), 1))),
            func.avg(sal),
        )
        .where(VacancyRow.company.isnot(None))
        .group_by(VacancyRow.company)
        .order_by(func.count().desc(), VacancyRow.company.asc())
        .limit(limit),
        query,
        dialect=dialect,
    )
    return [
        CompanyStats(
            company=str(company),
            vacancies=int(cnt),
            avg_score=float(avg_score) if avg_score is not None else None,
            with_salary=int(with_sal or 0),
            avg_salary_rub=float(avg_sal) if avg_sal is not None else None,
        )
        for company, cnt, avg_score, with_sal, avg_sal in session.execute(stmt).all()
    ]
