"""Жизнь вакансии: сколько текст остаётся на рынке по его публикациям в Telegram.

Срок — от первой до последней публикации текста (``vacancy_sightings``: оригинал, перепосты
и перепосты с правками). Это видимость, а не дата закрытия: вакансию, которую больше не
публиковали, могли и закрыть, и просто перестать продвигать. HH сюда не входит — его синк
берёт окно по дате публикации, и пропажа из выдачи не значит, что вакансию закрыли.
"""

from __future__ import annotations

from sqlalchemy import Date, Float, Integer, and_, cast, func, select, true
from sqlalchemy.orm import Session

from backend.db.company_profile import LONG_OPEN_DAYS
from backend.db.models import VacancySightingRow
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.db.vacancy_metrics import _vacancies_alias
from backend.schemas import (
    LifetimeAnalytics,
    LifetimeBucket,
    LifetimeCompany,
    LifetimeGroup,
    LifetimeVacancy,
    VacancyQuery,
)

# «Публикуют повторно» — текст выходил в столько разных дней (UTC).
REPUBLISHED_MIN_DAYS = 2
# Роль или грейд в разрезе — от стольких вакансий; меньше — доли шумят.
MIN_GROUP = 10
GROUPS_TOP = 15
COMPANIES_TOP = 15
LONGEST_TOP = 15
# Корзины срока в днях: (подпись, от, до включительно; None — без границы).
BUCKETS: list[tuple[str, int, int | None]] = [
    ("В один день", 0, 0),
    ("До недели", 1, 7),
    ("1–2 недели", 8, 14),
    ("2–4 недели", 15, 30),
    ("Больше месяца", 31, None),
]


def _per_vacancy(query: VacancyQuery):
    """CTE: вакансия под фильтрами + её публикации (первая, последняя, дни, посты, срок)."""
    v = _vacancies_alias()
    s = VacancySightingRow.__table__.alias("s")
    day = cast(func.timezone("UTC", s.c.post_date), Date)
    first_at = func.min(s.c.post_date)
    last_at = func.max(s.c.post_date)
    span = cast(func.floor(func.extract("epoch", last_at - first_at) / 86400), Integer)
    stmt = (
        select(
            v.c.id,
            v.c.company,
            v.c.company_key,
            v.c.detected_specializations,
            v.c.detected_grade,
            v.c.source_channel,
            v.c.archived_at,
            first_at.label("first_at"),
            last_at.label("last_at"),
            func.count(func.distinct(day)).label("days"),
            func.count().label("posts"),
            span.label("span"),
        )
        .select_from(v.join(s, s.c.vacancy_id == v.c.id))
        .where(s.c.post_date.isnot(None))
        .group_by(v.c.id)
    )
    return apply_vacancy_filters(stmt, query, table=v).cte("per_vacancy")


def _group_columns(per):
    republished = per.c.days >= REPUBLISHED_MIN_DAYS
    return (
        func.count(),
        func.count().filter(republished),
        func.count().filter(per.c.span >= LONG_OPEN_DAYS),
        cast(func.percentile_cont(0.5).within_group(per.c.span).filter(republished), Float),
    )


def _group(name: object, n: int, rep: int, long_open: int, median: float | None) -> LifetimeGroup:
    return LifetimeGroup(
        name=str(name or "unknown"),
        vacancies=int(n),
        republished=int(rep or 0),
        republished_share=(rep or 0) / n if n else 0.0,
        long_open=int(long_open or 0),
        median_span_days=median,
    )


def vacancy_lifetime(session: Session, query: VacancyQuery) -> LifetimeAnalytics:
    per = _per_vacancy(query)
    republished = per.c.days >= REPUBLISHED_MIN_DAYS

    n, rep, long_open, p50, p90 = session.execute(
        select(
            func.count(),
            func.count().filter(republished),
            func.count().filter(per.c.span >= LONG_OPEN_DAYS),
            cast(func.percentile_cont(0.5).within_group(per.c.span).filter(republished), Float),
            cast(func.percentile_cont(0.9).within_group(per.c.span).filter(republished), Float),
        ).select_from(per)
    ).one()
    n = int(n or 0)

    bucket_cols = [
        func.count().filter(and_(per.c.span >= lo, *([per.c.span <= hi] if hi is not None else [])))
        for _, lo, hi in BUCKETS
    ]
    bucket_counts = session.execute(select(*bucket_cols).select_from(per)).one()
    buckets = [
        LifetimeBucket(label=label, min_days=lo, max_days=hi, count=int(c or 0))
        for (label, lo, hi), c in zip(BUCKETS, bucket_counts, strict=True)
    ]

    role = (
        func.jsonb_array_elements_text(per.c.detected_specializations).table_valued("spec").render_derived(name="role")
    )
    role_rows = session.execute(
        select(role.c.spec, *_group_columns(per))
        .select_from(per.join(role, true()))
        .group_by(role.c.spec)
        .having(func.count() >= MIN_GROUP)
        .order_by(func.count().desc(), role.c.spec)
        .limit(GROUPS_TOP)
    ).all()
    grade_rows = session.execute(
        select(per.c.detected_grade, *_group_columns(per))
        .group_by(per.c.detected_grade)
        .having(func.count() >= MIN_GROUP)
        .order_by(func.count().desc(), per.c.detected_grade)
    ).all()

    company_name = func.mode().within_group(per.c.company)
    company_rows = session.execute(
        select(
            company_name,
            func.count(),
            func.count().filter(republished),
            func.count().filter(per.c.span >= LONG_OPEN_DAYS),
            func.max(per.c.span),
        )
        .where(per.c.company_key.isnot(None))
        .group_by(per.c.company_key)
        .having(func.count().filter(republished) > 0)
        .order_by(
            func.count().filter(republished).desc(),
            func.max(per.c.span).desc(),
            company_name,
        )
        .limit(COMPANIES_TOP)
    ).all()

    longest_rows = session.execute(
        select(per)
        .where(republished)
        .order_by(per.c.span.desc(), per.c.days.desc(), per.c.last_at.desc(), per.c.id)
        .limit(LONGEST_TOP)
    ).all()

    return LifetimeAnalytics(
        vacancies=n,
        republished=int(rep or 0),
        republished_share=(rep or 0) / n if n else 0.0,
        long_open=int(long_open or 0),
        long_open_days=LONG_OPEN_DAYS,
        span_p50_days=p50,
        span_p90_days=p90,
        buckets=buckets,
        by_role=[_group(*r) for r in role_rows],
        by_grade=[_group(*r) for r in grade_rows],
        companies=[
            LifetimeCompany(
                company=str(name),
                vacancies=int(cnt),
                republished=int(rep_c or 0),
                long_open=int(lo or 0),
                max_span_days=int(mx or 0),
            )
            for name, cnt, rep_c, lo, mx in company_rows
        ],
        longest=[
            LifetimeVacancy(
                id=r.id,
                company=r.company,
                specializations=list(r.detected_specializations or []),
                grade=r.detected_grade or "unknown",
                source_channel=r.source_channel,
                first_at=r.first_at,
                last_at=r.last_at,
                span_days=int(r.span or 0),
                publication_days=int(r.days or 0),
                posts=int(r.posts or 0),
                archived=r.archived_at is not None,
            )
            for r in longest_rows
        ],
        min_group=MIN_GROUP,
    )
