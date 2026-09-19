"""Матчинг резюме к вакансиям: резюме и оценки соответствия.

``vacancy_matches`` — PK (resume_id, vacancy_id): строка является и кэшем
матчинга (повторный прогон пропускает уже оценённые пары); колонка ``model``
фиксирует, какой моделью получен скор.

Revision ID: 006_resume_matching
Revises: 004_fts_and_saved_filters
Create Date: 2026-09-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_resume_matching"
down_revision: Union[str, None] = "004_fts_and_saved_filters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("skills_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "vacancy_matches",
        sa.Column(
            "resume_id",
            sa.Integer(),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "vacancy_id",
            sa.String(length=128),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("matching_skills", sa.JSON(), nullable=True),
        sa.Column("missing_skills", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_vacancy_matches_vacancy_id",
        "vacancy_matches",
        ["vacancy_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_vacancy_matches_vacancy_id", table_name="vacancy_matches")
    op.drop_table("vacancy_matches")
    op.drop_table("resumes")
