"""Полнотекстовый поиск по вакансиям и таблица сохранённых фильтров.

Функциональный GIN-индекс на ``to_tsvector('russian', cleaned_text)`` —
запросы вида ``to_tsvector('russian', cleaned_text) @@ query`` используют
его без отдельной generated-колонки (совместимо с SQLite в тестах).

Revision ID: 004_fts_and_saved_filters
Revises: 003_vacancy_archived_at
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_fts_and_saved_filters"
down_revision: Union[str, None] = "003_vacancy_archived_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # GIN/to_tsvector — только PostgreSQL; на SQLite (тесты) поиск идёт через ILIKE.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_vacancies_fts ON vacancies "
            "USING GIN (to_tsvector('russian', cleaned_text))"
        )
    op.create_table(
        "saved_filters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("saved_filters")
    op.execute("DROP INDEX IF EXISTS ix_vacancies_fts")
