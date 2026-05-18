"""Регрессия: LATERAL unnest специализаций должен коррелировать с внешней строкой."""

from __future__ import annotations

from sqlalchemy import cast, func, lateral, select, true
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from backend.db.models import VacancyRow
from backend.db.vacancy_metrics import _pg_spec_lateral, _vacancies_alias


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
