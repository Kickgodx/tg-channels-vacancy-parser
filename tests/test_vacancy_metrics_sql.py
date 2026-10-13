"""Регрессия: LATERAL unnest специализаций должен коррелировать с внешней строкой."""

from __future__ import annotations

from sqlalchemy import func, select, true
from sqlalchemy.dialects import postgresql

from backend.db.models import VacancyRow
from backend.db.vacancy_filters import apply_vacancy_filters
from backend.db.vacancy_metrics import (
    _pg_spec_lateral,
    _salary_rub_expr,
    _salary_rub_expr_table,
    _vacancies_alias,
)
from backend.schemas import VacancyQuery


def test_postgresql_lateral_unnest_is_correlated():
    v = _vacancies_alias()
    spec_lat = _pg_spec_lateral(v)
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
    spec_lat = _pg_spec_lateral(v)
    sal = _salary_rub_expr_table(v)
    stmt = apply_vacancy_filters(
        select(spec_lat.c.spec, func.avg(sal), func.count(v.c.id))
        .select_from(v.join(spec_lat, true()))
        .where(sal.isnot(None))
        .group_by(spec_lat.c.spec)
        .limit(25),
        query,
        table=v,
        dialect="postgresql",
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
            dialect="postgresql",
        ).compile(dialect=postgresql.dialect())
    ).lower()
    assert "exchange_rates" not in count_sql


def test_daily_buckets_keep_latest_days(monkeypatch, tmp_path):
    """Лимит точек графика отбрасывает самые старые дни, а не самые свежие."""
    import uuid
    from datetime import UTC, datetime

    from backend.db import vacancy_metrics
    from backend.db.repository import upsert_vacancies
    from backend.db.session import init_db, reset_engine, session_scope
    from backend.settings import get_settings
    from tg_jobs_parser.models.vacancy import Vacancy
    from tg_jobs_parser.utils.hashing import vacancy_content_id

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'daily.db').as_posix()}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    try:
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
            upsert_vacancies(session, vacancies, parse_run_id=uuid.uuid4())
            buckets = vacancy_metrics._daily_buckets(session, VacancyQuery(include_archived=True), dialect="sqlite")
        assert [b.period for b in buckets] == ["2026-03-02", "2026-03-03"]
    finally:
        reset_engine()
        get_settings.cache_clear()
