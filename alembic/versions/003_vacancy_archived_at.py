"""Soft delete вакансий: колонка archived_at вместо физического удаления по TTL.

Revision ID: 003_vacancy_archived_at
Revises: 002_vacancy_retention_index
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_vacancy_archived_at"
down_revision: Union[str, None] = "002_vacancy_retention_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vacancies", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_vacancies_archived_at", "vacancies", ["archived_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_vacancies_archived_at", table_name="vacancies")
    op.drop_column("vacancies", "archived_at")
