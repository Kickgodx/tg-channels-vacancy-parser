"""Расчёт метрик и агрегатов для отчётов."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from statistics import mean, median

from jobdesk.extraction.grade_extractor import GRADE_RANK
from jobdesk.models.metrics import (
    MetricsSummary,
    SalaryDisplayMeta,
    SalaryStats,
    TimeSeriesBucket,
)
from jobdesk.models.vacancy import Vacancy


def _salary_values_rub(vs: Iterable[Vacancy]) -> list[float]:
    """Суммы в RUB для графиков (только если удалось сконвертировать)."""
    out: list[float] = []
    for v in vs:
        if v.detected_salary_avg_rub is not None:
            out.append(float(v.detected_salary_avg_rub))
    return out


def _week_period(d: date) -> str:
    """Неделя пн–вс в виде ``YYYY-MM-DD/YYYY-MM-DD``."""
    start = d - timedelta(days=d.weekday())
    return f"{start.isoformat()}/{(start + timedelta(days=6)).isoformat()}"


def _buckets(periods: Iterable[str]) -> list[TimeSeriesBucket]:
    """Счётчик по периодам; ISO-строки сортируются хронологически."""
    return [TimeSeriesBucket(period=p, count=c) for p, c in sorted(Counter(periods).items())]


def _grade_rank(g: str | None) -> float:
    if not g or g == "unknown":
        return float("nan")
    return float(GRADE_RANK.get(g, 0))


def build_metrics(
    vacancies_all: list[Vacancy],
    vacancies_unique: list[Vacancy],
    duplicates: list[Vacancy],
    posts_collected: int,
) -> MetricsSummary:
    ms = MetricsSummary()
    ms.total_posts_fetched = posts_collected
    ms.total_vacancies = len(vacancies_all)
    ms.unique_vacancies = len(vacancies_unique)
    ms.duplicate_count = len(duplicates)

    by_ch: Counter[str] = Counter()
    by_spec: Counter[str] = Counter()
    by_grade: Counter[str] = Counter()
    by_country: Counter[str] = Counter()
    by_city: Counter[str] = Counter()
    by_wf: Counter[str] = Counter()

    for v in vacancies_unique:
        by_ch[v.source_channel] += 1
        for s in v.detected_specializations:
            by_spec[s] += 1
        by_grade[v.detected_grade or "unknown"] += 1
        if v.country:
            by_country[v.country] += 1
        if v.city:
            by_city[v.city] += 1
        wf_key = v.work_format or "unknown"
        by_wf[wf_key] += 1

    ms.vacancies_by_channel = dict(by_ch)
    ms.vacancies_by_specialization = dict(by_spec)
    ms.vacancies_by_grade = dict(by_grade)
    ms.vacancies_by_country = dict(by_country)
    ms.vacancies_by_city = dict(by_city)
    ms.vacancies_by_work_format = dict(by_wf)

    ms.grade_distribution = dict(by_grade)

    # mean grade rank by specialization
    spec_ranks: dict[str, list[float]] = defaultdict(list)
    for v in vacancies_unique:
        rnk = _grade_rank(v.detected_grade)
        for s in v.detected_specializations:
            if rnk == rnk:  # not nan
                spec_ranks[s].append(rnk)
    for s, ranks in spec_ranks.items():
        ms.mean_grade_rank_by_specialization[s] = float(mean(ranks)) if ranks else 0.0

    # salary stats
    def stats_for(vs: list[Vacancy]) -> SalaryStats:
        vals = _salary_values_rub(vs)
        if not vals:
            return SalaryStats(count_with_salary=0)
        return SalaryStats(
            mean=float(mean(vals)),
            median=float(median(vals)),
            count_with_salary=len(vals),
        )

    for spec, _ in by_spec.items():
        vs = [v for v in vacancies_unique if spec in v.detected_specializations]
        ms.salary_by_specialization[spec] = stats_for(vs)

    for grade, _ in by_grade.items():
        vs = [v for v in vacancies_unique if v.detected_grade == grade]
        ms.salary_by_grade[grade] = stats_for(vs)

    for spec in by_spec:
        for grade in by_grade:
            key = f"{spec} / {grade}"
            vs = [v for v in vacancies_unique if spec in v.detected_specializations and v.detected_grade == grade]
            st = stats_for(vs)
            if st.count_with_salary or vs:
                ms.salary_by_specialization_grade[key] = st

    ms.vacancies_with_salary = sum(1 for v in vacancies_unique if bool(_salary_values_rub([v])))
    ms.vacancies_without_salary = len(vacancies_unique) - ms.vacancies_with_salary
    ms.salary_display = SalaryDisplayMeta(display_currency="RUB", rates_source="cbr")

    # time series
    dates: list[datetime] = []
    for v in vacancies_unique:
        if v.post_date:
            dates.append(v.post_date)
    if dates:
        days = [(d.astimezone(UTC) if d.tzinfo else d).date() for d in dates]
        ms.daily = _buckets(d.isoformat() for d in days)
        ms.weekly = _buckets(_week_period(d) for d in days)
        ms.monthly = _buckets(f"{d.year:04d}-{d.month:02d}" for d in days)

    # technologies
    tech_counter: Counter[str] = Counter()
    tech_by_spec: dict[str, Counter[str]] = defaultdict(Counter)
    for v in vacancies_unique:
        for t in v.technologies:
            tech_counter[t] += 1
            for s in v.detected_specializations:
                tech_by_spec[s][t] += 1

    ms.technology_top = dict(tech_counter.most_common(50))
    ms.technology_mentions = dict(tech_counter)
    ms.technology_by_specialization = {s: dict(c.most_common(30)) for s, c in tech_by_spec.items()}

    return ms
