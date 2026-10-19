"""Регрессия: LATERAL unnest специализаций должен коррелировать с внешней строкой."""

from __future__ import annotations

from sqlalchemy import func, select, true
from sqlalchemy.dialects import postgresql
from tests.conftest import new_parse_run

from backend.db.models import VacancyRow
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.db.vacancy_metrics import (
    _salary_rub_expr,
    _salary_rub_expr_table,
    _spec_lateral,
    _vacancies_alias,
)
from backend.schemas import VacancyQuery


def test_postgresql_lateral_unnest_is_correlated():
    v = _vacancies_alias()
    spec_lat = _spec_lateral(v)
    stmt = select(spec_lat.c.spec, func.count(v.c.id)).select_from(v.join(spec_lat, true()))
    sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "JOIN LATERAL" in sql
    assert "AS v" in sql or "vacancies AS v" in sql
    # Без correlate() SQLAlchemy вставляет второй FROM vacancies внутрь LATERAL.
    assert "LATERAL (SELECT" in sql
    lateral_part = sql.split("JOIN LATERAL", 1)[1].split("ON true", 1)[0]
    assert "FROM vacancies" not in lateral_part


def test_salary_metrics_sql_uses_stored_rub_without_fx_subquery():
    """Агрегаты зарплат — только detected_salary_avg_rub (как metrics_builder).

    Correlated subquery в exchange_rates раздувает cost → Postgres LLVM JIT
    (~1s компиляции на ~20ms работы) и убивает CPU под concurrency.
    """
    query = VacancyQuery(page=1, page_size=1, include_archived=False)
    v = _vacancies_alias()
    spec_lat = _spec_lateral(v)
    sal = _salary_rub_expr_table(v)
    stmt = apply_vacancy_filters(
        select(spec_lat.c.spec, func.avg(sal), func.count(v.c.id))
        .select_from(v.join(spec_lat, true()))
        .where(sal.isnot(None))
        .group_by(spec_lat.c.spec)
        .limit(25),
        query,
        table=v,
    )
    sql = str(stmt.compile(dialect=postgresql.dialect())).lower()
    assert "detected_salary_avg_rub" in sql
    assert "exchange_rates" not in sql
    assert "rate_to_rub" not in sql

    sal_orm = _salary_rub_expr()
    count_sql = str(
        apply_vacancy_filters(
            select(func.count(VacancyRow.id)).where(sal_orm.isnot(None)),
            query,
        ).compile(dialect=postgresql.dialect())
    ).lower()
    assert "exchange_rates" not in count_sql


def test_daily_buckets_keep_latest_days(monkeypatch, pg_db):
    """Лимит точек графика отбрасывает самые старые дни, а не самые свежие."""
    from datetime import UTC, datetime

    from backend.db import vacancy_metrics
    from backend.db.repository import upsert_vacancies
    from backend.db.session import session_scope
    from tg_jobs_parser.models.vacancy import Vacancy
    from tg_jobs_parser.utils.hashing import vacancy_content_id

    vacancies = []
    for day in (1, 2, 3):
        text = f"Python dev, day {day}"
        vacancies.append(
            Vacancy(
                id=vacancy_content_id(text),
                source_post_key=f"chan-{day}",
                source_channel="chan",
                source_url="https://t.me/s/chan",
                post_date=datetime(2026, 3, day, 12, tzinfo=UTC),
                raw_text=text,
                cleaned_text=text,
                detected_specializations=["Python"],
                detected_grade="middle",
            )
        )
    monkeypatch.setattr(vacancy_metrics, "_MAX_DAILY_BUCKETS", 2)
    with session_scope() as session:
        upsert_vacancies(session, vacancies, parse_run_id=new_parse_run(session))
        buckets = vacancy_metrics._daily_buckets(session, VacancyQuery(include_archived=True))
    assert [b.period for b in buckets] == ["2026-03-02", "2026-03-03"]


def test_metrics_and_salary_analytics_query_count(db_session):
    """Агрегаты собраны в несколько запросов (GROUPING SETS), а не по запросу на разрез.

    Курсы в тестах выключены — запросов к ``exchange_rates`` нет.
    """
    from datetime import UTC, datetime

    from sqlalchemy import event

    from backend.db.analytics import salary_analytics
    from backend.db.repository import upsert_vacancies
    from backend.db.session import get_engine
    from backend.db.vacancy_metrics import aggregate_vacancy_metrics
    from tg_jobs_parser.models.vacancy import Vacancy

    upsert_vacancies(
        db_session,
        [
            Vacancy(
                id=f"v{i}",
                source_post_key=f"c-{i}",
                source_channel="c",
                source_url="https://t.me/s/c",
                post_date=datetime(2026, 9, 1 + i, tzinfo=UTC),
                raw_text=f"t{i}",
                cleaned_text=f"t{i}",
                detected_specializations=["Backend"],
                detected_grade=grade,
                work_format=fmt,
                detected_salary_avg=200_000.0,
                detected_salary_avg_rub=200_000.0,
                salary_currency="RUB",
            )
            for i, (grade, fmt) in enumerate([("senior", "remote"), ("middle", None)])
        ],
        parse_run_id=new_parse_run(db_session),
    )
    db_session.commit()

    statements: list[str] = []

    def _count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        metrics = aggregate_vacancy_metrics(db_session, VacancyQuery())
        n_metrics = len(statements)
        salaries = salary_analytics(db_session, VacancyQuery())
        n_salaries = len(statements) - n_metrics
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert n_metrics == 3  # грейды/форматы/итоги, специализации, дни
    assert n_salaries == 5  # грейд+формат, специализации, периоды, периоды × группы, распределение
    assert metrics.unique_vacancies == 2 and metrics.vacancies_with_salary == 2
    assert metrics.vacancies_by_work_format == {"remote": 1, "unknown": 1}
    assert metrics.salary_by_grade["senior"].count_with_salary == 1
    assert [g.group for g in salaries.by_work_format] == ["remote", "unknown"]
    assert {b.group for b in salaries.by_grade_monthly} == {"senior", "middle"}
    assert [b.period for b in salaries.yearly] == ["2026"]
