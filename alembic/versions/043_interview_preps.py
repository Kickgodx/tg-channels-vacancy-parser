"""Подготовка к собеседованию: вероятные вопросы по вакансии и тезисы ответов из резюме.

``interview_preps`` — PK (resume_id, vacancy_id), как у ``resume_tailorings``: строка — и кэш
генерации. ``requested_model`` + ``prompt_version`` — для ``stale``.

Revision ID: 043_interview_preps
Revises: 042_embeddings
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "043_interview_preps"
down_revision: Union[str, None] = "042_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "interview_preps",
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("questions_to_ask", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("requested_model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("resume_id", "vacancy_id", name="pk_interview_preps"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE", name="fk_interview_preps_resume_id"),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancies.id"], ondelete="CASCADE", name="fk_interview_preps_vacancy_id"
        ),
    )
    op.create_index("ix_interview_preps_vacancy_id", "interview_preps", ["vacancy_id"])


def downgrade() -> None:
    op.drop_index("ix_interview_preps_vacancy_id", table_name="interview_preps")
    op.drop_table("interview_preps")
