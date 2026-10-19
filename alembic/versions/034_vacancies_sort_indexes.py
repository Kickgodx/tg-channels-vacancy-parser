"""Частичные индексы активных вакансий под сортировку «дата + зарплата ₽».

``ix_vacancies_active_day_salary`` — ``sort_by=post_date&then_by=detected_salary_avg_rub``
(день поста по UTC ↓, зарплата ↓, время ↓, id). ``ix_vacancies_active_salary_date`` —
сортировка по зарплате ↓ (одна или с ``then_by=post_date``): зарплата ↓, дата ↓, id.
Оба повторяют ORDER BY списка целиком — Index Scan с остановкой на LIMIT, без Sort.

Revision ID: 034_vacancies_sort_indexes
Revises: 033_vacancies_search_tsv
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "034_vacancies_sort_indexes"
down_revision: Union[str, None] = "033_vacancies_search_tsv"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE = "WHERE is_duplicate IS false AND archived_at IS NULL"


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vacancies_active_day_salary ON vacancies ("
        "((post_date AT TIME ZONE 'UTC')::date) DESC NULLS LAST, "
        "detected_salary_avg_rub DESC NULLS LAST, post_date DESC NULLS LAST, id ASC) " + _ACTIVE
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vacancies_active_salary_date ON vacancies ("
        "detected_salary_avg_rub DESC NULLS LAST, post_date DESC NULLS LAST, id ASC) " + _ACTIVE
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vacancies_active_salary_date")
    op.execute("DROP INDEX IF EXISTS ix_vacancies_active_day_salary")
