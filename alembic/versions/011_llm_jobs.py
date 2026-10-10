"""Фоновые LLM-джобы мэтчинга и сопроводительных писем.

Revision ID: 011_llm_jobs
Revises: 010_hh_listings
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011_llm_jobs"
down_revision: Union[str, None] = "010_hh_listings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "resume_id",
            sa.Integer(),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vacancy_id",
            sa.String(length=128),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("force", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_llm_jobs_user_id", "llm_jobs", ["user_id"], unique=False)
    op.create_index("ix_llm_jobs_status", "llm_jobs", ["status"], unique=False)
    op.create_index(
        "ix_llm_jobs_user_status",
        "llm_jobs",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_jobs_user_status", table_name="llm_jobs")
    op.drop_index("ix_llm_jobs_status", table_name="llm_jobs")
    op.drop_index("ix_llm_jobs_user_id", table_name="llm_jobs")
    op.drop_table("llm_jobs")
