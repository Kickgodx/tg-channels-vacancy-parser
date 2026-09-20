"""Матчинг v2: breakdown-оценки, confidence, strengths/risks.

``score`` переименован в ``overall_score`` (теперь его считает backend, не LLM).
Структура matching/missing_skills меняется (строки -> объекты с importance и
evidence), поэтому старые строки кэша матчинга очищаются — пары будут
переоценены при следующем запросе.

Revision ID: 008_match_breakdown
Revises: 007_auth_multiuser
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_match_breakdown"
down_revision: Union[str, None] = "007_auth_multiuser"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM vacancy_matches")
    with op.batch_alter_table("vacancy_matches") as batch:
        batch.alter_column(
            "score",
            new_column_name="overall_score",
            existing_type=sa.Float(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
        batch.add_column(sa.Column("confidence", sa.Integer(), nullable=False))
        batch.add_column(sa.Column("breakdown", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("strengths", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("risks", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vacancy_matches") as batch:
        batch.drop_column("risks")
        batch.drop_column("strengths")
        batch.drop_column("breakdown")
        batch.drop_column("confidence")
        batch.alter_column(
            "overall_score",
            new_column_name="score",
            existing_type=sa.Integer(),
            type_=sa.Float(),
            existing_nullable=False,
        )
