"""Аналитика технологий: зарплатная премия с поправкой на грейд, спрос по месяцам, рост и падение.

Свой фильтр по технологии не применяется (как у фасета ``/vacancies/technologies``):
премия Python под фильтром «Python» не имеет смысла — без Python вакансий там нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Float, and_, cast, func, literal_column, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from backend.db.vacancy_filters import apply_vacancy_filters
from backend.db.vacancy_metrics import _prepare_fx, _salary_rub_expr_table, _tech_lateral, _vacancies_alias
from backend.schemas import (
    TechnologyAnalytics,
    TechnologyGradeStats,
    TechnologyMomentum,
    TechnologyPremium,
    TechnologySeries,
    TechnologyTrend,
    VacancyQuery,
)

# Сколько технологий оценивать по премии (самые частые среди вакансий с зарплатой).
PREMIUM_TOP = 25
# В грейде сравниваем, только если и с технологией, и без неё не меньше стольких вакансий.
PREMIUM_MIN_STRATUM = 5
# Премия не считается, если в сопоставимых грейдах меньше стольких вакансий с технологией.
PREMIUM_MIN_VACANCIES = 10
# Линии спроса по месяцам — самые частые технологии; не больше стольких последних месяцев.
TREND_TOP = 8
TREND_MAX_MONTHS = 24
# Рост и падение: последнее окно против предыдущего такой же длины, считая от последней даты поста.
MOMENTUM_WINDOW_DAYS = 30
MOMENTUM_TOP = 30
MOMENTUM_MIN_WINDOW_VACANCIES = 30


@dataclass(frozen=True)
class Stratum:
    grade: str
    with_count: int
    with_median: float | None
    without_count: int
    without_median: float | None


def stratum_comparable(s: Stratum) -> bool:
    return (
        s.with_count >= PREMIUM_MIN_STRATUM
        and s.without_count >= PREMIUM_MIN_STRATUM
        and bool(s.with_median)
        and bool(s.without_median)
    )


def grade_adjusted_premium(strata: list[Stratum]) -> tuple[float | None, int]:
    """Премия = средневзвешенное по числу вакансий с технологией (медиана с / медиана без − 1)
    по грейдам, где обе группы не меньше ``PREMIUM_MIN_STRATUM``.

    Возвращает (премия или None, сколько вакансий с технологией вошло в сравнение).
    """
    weight = 0
    total = 0.0
    for s in strata:
        if not stratum_comparable(s):
            continue
        weight += s.with_count
        total += s.with_count * (s.with_median / s.without_median - 1.0)
    if weight < PREMIUM_MIN_VACANCIES:
        return None, weight
    return total / weight, weight


def _without_own_filter(query: VacancyQuery) -> VacancyQuery:
    return query.model_copy(update={"technologies": []})


def _top_technologies(session: Session, query: VacancyQuery, *, limit: int, salaried: bool) -> list[str]:
    v = _vacancies_alias()
    lat = _tech_lateral(v)
    n = func.count(v.c.id)
    stmt = select(lat.c.tech, n).select_from(v.join(lat, true())).group_by(lat.c.tech)
    if salaried:
        stmt = stmt.where(_salary_rub_expr_table(v).isnot(None))
    stmt = apply_vacancy_filters(stmt.order_by(n.desc(), lat.c.tech).limit(limit), query, table=v)
    return [str(t) for t, _ in session.execute(stmt).all() if t]


def _tech_values(techs: list[str]):
    """``unnest(ARRAY[...]) AS t(tech)`` — список технологий как таблица для CROSS JOIN."""
    return func.unnest(cast(techs, ARRAY(Text))).table_valued("tech").render_derived(name="t")


def _premium(session: Session, query: VacancyQuery, techs: list[str] | None = None) -> list[TechnologyPremium]:
    """Премия технологий под фильтрами: заданных или ``PREMIUM_TOP`` самых частых среди вакансий с зарплатой."""
    if techs is None:
        techs = _top_technologies(session, query, limit=PREMIUM_TOP, salaried=True)
    if not techs:
        return []
    v = _vacancies_alias()
    t = _tech_values(techs)
    sal = _salary_rub_expr_table(v)
    has = v.c.technologies.op("@>")(func.jsonb_build_array(t.c.tech))
    median = func.percentile_cont(0.5).within_group(sal)
    stmt = apply_vacancy_filters(
        select(
            t.c.tech,
            v.c.detected_grade,
            func.count().filter(has),
            cast(median.filter(has), Float),
            func.count().filter(~has),
            cast(median.filter(~has), Float),
        )
        .select_from(v.join(t, true()))
        .where(sal.isnot(None))
        .group_by(t.c.tech, v.c.detected_grade),
        query,
        table=v,
    )
    strata: dict[str, list[Stratum]] = {tech: [] for tech in techs}
    for tech, grade, n_with, med_with, n_without, med_without in session.execute(stmt).all():
        strata[str(tech)].append(
            Stratum(
                grade=str(grade or "unknown"),
                with_count=int(n_with or 0),
                with_median=med_with,
                without_count=int(n_without or 0),
                without_median=med_without,
            )
        )

    overall = _overall_medians(session, query, techs)
    out: list[TechnologyPremium] = []
    for tech in techs:
        rows = sorted(strata[tech], key=lambda s: s.grade)
        premium, compared = grade_adjusted_premium(rows)
        n, med = overall.get(tech, (0, None))
        out.append(
            TechnologyPremium(
                technology=tech,
                vacancies=n,
                median=med,
                premium=premium,
                compared_vacancies=compared,
                by_grade=[
                    TechnologyGradeStats(
                        grade=s.grade,
                        with_count=s.with_count,
                        with_median=s.with_median,
                        without_count=s.without_count,
                        without_median=s.without_median,
                        compared=stratum_comparable(s),
                    )
                    for s in rows
                    if s.with_count
                ],
            )
        )
    return out


def technology_premiums(session: Session, query: VacancyQuery, techs: list[str]) -> list[TechnologyPremium]:
    """Премия с поправкой на грейд для заданных технологий (для «Что если выучить»)."""
    return _premium(session, query, techs) if techs else []


def _overall_medians(session: Session, query: VacancyQuery, techs: list[str]) -> dict[str, tuple[int, float | None]]:
    v = _vacancies_alias()
    lat = _tech_lateral(v)
    sal = _salary_rub_expr_table(v)
    stmt = apply_vacancy_filters(
        select(lat.c.tech, func.count(), cast(func.percentile_cont(0.5).within_group(sal), Float))
        .select_from(v.join(lat, true()))
        .where(sal.isnot(None), lat.c.tech.in_(techs))
        .group_by(lat.c.tech),
        query,
        table=v,
    )
    return {str(tech): (int(n), med) for tech, n, med in session.execute(stmt).all()}


def _month(v):
    return func.date_trunc(literal_column("'month'"), v.c.post_date)


def _trend(session: Session, query: VacancyQuery) -> TechnologyTrend:
    v = _vacancies_alias()
    month = _month(v)
    totals_stmt = apply_vacancy_filters(
        select(month, func.count()).where(v.c.post_date.isnot(None)).group_by(month), query, table=v
    )
    totals = {m: int(n) for m, n in session.execute(totals_stmt).all() if m is not None}
    if not totals:
        return TechnologyTrend()
    months = sorted(totals)[-TREND_MAX_MONTHS:]
    techs = _top_technologies(session, query, limit=TREND_TOP, salaried=False)
    counts: dict[str, dict] = {tech: {} for tech in techs}
    if techs:
        v2 = _vacancies_alias()
        lat = _tech_lateral(v2)
        month2 = _month(v2)
        stmt = apply_vacancy_filters(
            select(lat.c.tech, month2, func.count())
            .select_from(v2.join(lat, true()))
            .where(v2.c.post_date.isnot(None), lat.c.tech.in_(techs), month2 >= months[0])
            .group_by(lat.c.tech, month2),
            query,
            table=v2,
        )
        for tech, m, n in session.execute(stmt).all():
            counts[str(tech)][m] = int(n)
    return TechnologyTrend(
        months=[m.strftime("%Y-%m") for m in months],
        totals=[totals[m] for m in months],
        series=[TechnologySeries(technology=tech, counts=[counts[tech].get(m, 0) for m in months]) for tech in techs],
    )


def _momentum(session: Session, query: VacancyQuery) -> tuple[list[TechnologyMomentum], object]:
    v = _vacancies_alias()
    anchor = session.execute(apply_vacancy_filters(select(func.max(v.c.post_date)), query, table=v)).scalar()
    if anchor is None:
        return [], None
    window = timedelta(days=MOMENTUM_WINDOW_DAYS)
    recent_from, prev_from = anchor - window, anchor - 2 * window

    def _in_recent(table):
        return table.c.post_date > recent_from

    def _in_prev(table):
        return and_(table.c.post_date > prev_from, table.c.post_date <= recent_from)

    totals_stmt = apply_vacancy_filters(
        select(func.count().filter(_in_recent(v)), func.count().filter(_in_prev(v))).where(v.c.post_date > prev_from),
        query,
        table=v,
    )
    recent_total, prev_total = session.execute(totals_stmt).one()
    if recent_total < MOMENTUM_MIN_WINDOW_VACANCIES or prev_total < MOMENTUM_MIN_WINDOW_VACANCIES:
        return [], anchor.date()

    v2 = _vacancies_alias()
    lat = _tech_lateral(v2)
    n = func.count()
    stmt = apply_vacancy_filters(
        select(lat.c.tech, n.filter(_in_recent(v2)), n.filter(_in_prev(v2)))
        .select_from(v2.join(lat, true()))
        .where(v2.c.post_date > prev_from)
        .group_by(lat.c.tech)
        .order_by(n.desc(), lat.c.tech)
        .limit(MOMENTUM_TOP),
        query,
        table=v2,
    )
    out = []
    for tech, recent, prev in session.execute(stmt).all():
        if not tech:
            continue
        rs, ps = recent / recent_total, prev / prev_total
        out.append(
            TechnologyMomentum(
                technology=str(tech),
                recent=int(recent),
                previous=int(prev),
                recent_share=rs,
                previous_share=ps,
                delta_pp=(rs - ps) * 100,
            )
        )
    out.sort(key=lambda m: (-m.delta_pp, m.technology))
    return out, anchor.date()


def technology_analytics(session: Session, query: VacancyQuery) -> TechnologyAnalytics:
    salary_display = _prepare_fx(session)
    query = _without_own_filter(query)
    momentum, anchor = _momentum(session, query)
    return TechnologyAnalytics(
        premium=_premium(session, query),
        trend=_trend(session, query),
        momentum=momentum,
        momentum_window_days=MOMENTUM_WINDOW_DAYS,
        momentum_anchor=anchor,
        salary_display=salary_display,
    )
