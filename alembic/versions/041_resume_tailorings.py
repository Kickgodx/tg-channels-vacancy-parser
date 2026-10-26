"""Адаптация резюме под вакансию: LLM-советы, как переписать резюме под требования.

``resume_tailorings`` — PK (resume_id, vacancy_id), как у ``cover_letters``: строка — и кэш
генерации. ``requested_model`` + ``prompt_version`` — для ``stale``.

Revision ID: 041_resume_tailorings
Revises: 040_vacancies_company_key
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "041_resume_tailorings"
down_revision: Union[str, None] = "040_vacancies_company_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resume_tailorings",
        sa.Column("resume_id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("do_not_claim", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("requested_model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("resume_id", "vacancy_id", name="pk_resume_tailorings"),
        sa.ForeignKeyConstraint(
            ["resume_id"], ["resumes.id"], ondelete="CASCADE", name="fk_resume_tailorings_resume_id"
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancies.id"], ondelete="CASCADE", name="fk_resume_tailorings_vacancy_id"
        ),
    )
    op.create_index("ix_resume_tailorings_vacancy_id", "resume_tailorings", ["vacancy_id"])


def downgrade() -> None:
    op.drop_index("ix_resume_tailorings_vacancy_id", table_name="resume_tailorings")
    op.drop_table("resume_tailorings")
