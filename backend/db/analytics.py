"""Аналитика дашборда: зарплатные квантили, качество источников, топ компаний."""

from __future__ import annotations

from sqlalchemy import case, func, literal, literal_column, select, true, tuple_
from sqlalchemy.orm import Session

from backend.db.models import VacancyRow, VacancySightingRow
from backend.db.vacancy_filters import apply_vacancy_filters, sighting_filter_conditions
from backend.db.vacancy_metrics import (
    _prepare_fx,
    _salary_rub_expr,
    _salary_rub_expr_table,
    _spec_lateral,
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

PeriodGrain = str  # "year" | "month" — первый аргумент date_trunc

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


def _group_stats(
    session: Session,
    query: VacancyQuery,
    group_expr,
    *,
    table=None,
    from_clause=None,
) -> list[SalaryGroupStats]:
    """Квантили через percentile_cont WITHIN GROUP; по убыванию числа вакансий."""
    sal = _salary_rub_expr_table(table) if table is not None else _salary_rub_expr()
    stmt = select(group_expr.label("grp"), *_quantile_columns(sal)).where(sal.isnot(None))
    if from_clause is not None:
        stmt = stmt.select_from(from_clause)
    stmt = apply_vacancy_filters(stmt.group_by(group_expr), query, table=table)
    out = [_group_row(grp, *stats) for grp, *stats in session.execute(stmt).all() if grp is not None and stats[0]]
    out.sort(key=lambda r: (-r.count, r.group))
    return out


def _f(value) -> float | None:
    return float(value) if value is not None else None


def _quantile_columns(sal) -> list:
    """count, mean, min, q1, median, q3, max — в порядке полей ``SalaryGroupStats``."""
    return [
        func.count(),
        func.avg(sal),
        func.min(sal),
        func.percentile_cont(0.25).within_group(sal),
        func.percentile_cont(0.5).within_group(sal),
        func.percentile_cont(0.75).within_group(sal),
        func.max(sal),
    ]


def _group_row(grp, n, mean_v, min_v, q1, med, q3, max_v) -> SalaryGroupStats:
    return SalaryGroupStats(
        group=str(grp),
        count=int(n),
        mean=_f(mean_v),
        min=_f(min_v),
        q1=_f(q1),
        median=_f(med),
        q3=_f(q3),
        max=_f(max_v),
    )


def _grade_and_format_stats(session: Session, query: VacancyQuery, grade_expr, wf_expr):
    """Квантили по грейду и по формату одним запросом (``GROUPING SETS``)."""
    sal = _salary_rub_expr()
    stmt = apply_vacancy_filters(
        select(func.grouping(grade_expr), grade_expr, wf_expr, *_quantile_columns(sal))
        .where(sal.isnot(None))
        .group_by(func.grouping_sets(tuple_(grade_expr), tuple_(wf_expr))),
        query,
    )
    by_grade: list[SalaryGroupStats] = []
    by_format: list[SalaryGroupStats] = []
    for g_grade, grade_v, wf_v, *stats in session.execute(stmt).all():
        grp, target = (wf_v, by_format) if g_grade else (grade_v, by_grade)
        if grp is not None and stats[0]:
            target.append(_group_row(grp, *stats))
    for rows in (by_grade, by_format):
        rows.sort(key=lambda r: (-r.count, r.group))
    return by_grade, by_format


def _salary_by_spec(session: Session, query: VacancyQuery) -> list[SalaryGroupStats]:
    v = _vacancies_alias()
    spec_lat = _spec_lateral(v)
    rows = _group_stats(session, query, spec_lat.c.spec, table=v, from_clause=v.join(spec_lat, true()))
    return rows[:_TOP_SALARY_SPECS]


def _salary_periods(
    session: Session, query: VacancyQuery
) -> tuple[list[SalaryMonthlyBucket], list[SalaryMonthlyBucket]]:
    """Число, среднее и медиана зарплаты по месяцам и по годам — один запрос."""
    sal = _salary_rub_expr()
    month = func.date_trunc(literal_column("'month'"), VacancyRow.post_date)
    year = func.date_trunc(literal_column("'year'"), VacancyRow.post_date)
    stmt = apply_vacancy_filters(
        select(
            func.grouping(month),
            month,
            year,
            func.count(),
            func.avg(sal),
            func.percentile_cont(0.5).within_group(sal),
        )
        .where(VacancyRow.post_date.isnot(None), sal.isnot(None))
        .group_by(func.grouping_sets(tuple_(month), tuple_(year))),
        query,
    )
    monthly: list[SalaryMonthlyBucket] = []
    yearly: list[SalaryMonthlyBucket] = []
    for g_month, m, y, n, mean_v, med in session.execute(stmt).all():
        p, fmt, target = (y, "%Y", yearly) if g_month else (m, "%Y-%m", monthly)
        if p is not None:
            target.append(SalaryMonthlyBucket(period=p.strftime(fmt), count=int(n), mean=_f(mean_v), median=_f(med)))
    monthly.sort(key=lambda b: b.period)
    yearly.sort(key=lambda b: b.period)
    return monthly, yearly


def _salary_period_groups(session: Session, query: VacancyQuery, grade_expr, wf_expr) -> dict[str, list]:
    """Квантили по (год | месяц) × (грейд | формат) — четыре разреза одним запросом."""
    sal = _salary_rub_expr()
    month = func.date_trunc(literal_column("'month'"), VacancyRow.post_date)
    year = func.date_trunc(literal_column("'year'"), VacancyRow.post_date)
    stmt = apply_vacancy_filters(
        select(
            func.grouping(month),
            func.grouping(grade_expr),
            month,
            year,
            grade_expr,
            wf_expr,
            *_quantile_columns(sal),
        )
        .where(VacancyRow.post_date.isnot(None), sal.isnot(None))
        .group_by(
            func.grouping_sets(
                tuple_(year, grade_expr),
                tuple_(year, wf_expr),
                tuple_(month, grade_expr),
                tuple_(month, wf_expr),
            )
        ),
        query,
    )
    out: dict[str, list[SalaryPeriodGroupBucket]] = {
        "by_grade_yearly": [],
        "by_work_format_yearly": [],
        "by_grade_monthly": [],
        "by_work_format_monthly": [],
    }
    for g_month, g_grade, m, y, grade_v, wf_v, n, *stats in session.execute(stmt).all():
        grain = "yearly" if g_month else "monthly"
        p, fmt = (y, "%Y") if g_month else (m, "%Y-%m")
        dim, grp = ("work_format", wf_v) if g_grade else ("grade", grade_v)
        if p is None or grp is None or not n:
            continue
        row = _group_row(grp, n, *stats)
        out[f"by_{dim}_{grain}"].append(SalaryPeriodGroupBucket(period=p.strftime(fmt), **row.model_dump()))
    for buckets in out.values():
        buckets.sort(key=lambda b: (b.period, b.group))
    return out


def _salary_distribution(session: Session, query: VacancyQuery) -> list[SalaryDistributionBucket]:
    sal = _salary_rub_expr()
    band = case(
        *[(sal < edge, literal(label)) for edge, label in _SALARY_BANDS[:-1]],
        else_=literal(_SALARY_BANDS[-1][1]),
    )
    stmt = apply_vacancy_filters(select(band.label("band"), func.count()).where(sal.isnot(None)).group_by(band), query)
    counts = {str(b): int(c) for b, c in session.execute(stmt).all()}
    return [SalaryDistributionBucket(range=label, count=counts.get(label, 0)) for _, label in _SALARY_BANDS]


def salary_analytics(session: Session, query: VacancyQuery) -> SalaryAnalytics:
    """Зарплатная аналитика по фильтрам списка вакансий."""
    salary_display = _prepare_fx(session)
    wf_expr = func.coalesce(VacancyRow.work_format, literal_column("'unknown'"))
    grade_expr = VacancyRow.detected_grade
    by_grade, by_work_format = _grade_and_format_stats(session, query, grade_expr, wf_expr)
    monthly, yearly = _salary_periods(session, query)
    return SalaryAnalytics(
        by_grade=by_grade,
        by_work_format=by_work_format,
        by_specialization=_salary_by_spec(session, query),
        monthly=monthly,
        yearly=yearly,
        **_salary_period_groups(session, query, grade_expr, wf_expr),
        distribution=_salary_distribution(session, query),
        salary_display=salary_display,
    )


def source_quality(session: Session, query: VacancyQuery) -> list[SourceStats]:
    """По каждому каналу: уникальные вакансии, дубли, доля уникальных постов, score, зарплата.

    ``vacancies``, score и зарплата — по строкам ``vacancies`` канала. Дубли и
    ``unique_share`` для Telegram — по ``vacancy_sightings``: доля постов канала,
    чей текст до этого не встречался (ни в другом канале, ни у него самого).
    У HH появлений нет — там дубли это строки ``is_duplicate`` (мультигород).
    """
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
        .where(*sighting_filter_conditions(query))
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
