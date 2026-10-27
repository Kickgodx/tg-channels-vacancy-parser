"""Изменения перепоста: ``vacancy_sightings.changes``.

У появления — перепоста с правками (нечёткий дубль) — отличия от первой публикации: зарплата,
формат, город, технологии. NULL — изменений нет или это не перепост с правками.

Revision ID: 047_sighting_changes
Revises: 046_vacancy_duplicate_of
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "047_sighting_changes"
down_revision: Union[str, None] = "046_vacancy_duplicate_of"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vacancy_sightings", sa.Column("changes", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("vacancy_sightings", "changes")
