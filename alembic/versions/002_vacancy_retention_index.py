"""Добавить индекс для удаления вакансий по TTL.

Revision ID: 002_vacancy_retention_index
Revises: 001_initial
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002_vacancy_retention_index"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_vacancies_parsed_at", "vacancies", ["parsed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vacancies_parsed_at", table_name="vacancies")
