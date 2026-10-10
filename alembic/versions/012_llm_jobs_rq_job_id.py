"""Связь llm_jobs с RQ job id для идемпотентного recover.

Revision ID: 012_llm_jobs_rq_job_id
Revises: 011_llm_jobs
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012_llm_jobs_rq_job_id"
down_revision: Union[str, None] = "011_llm_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_jobs", sa.Column("rq_job_id", sa.String(length=64), nullable=True))
    op.create_index("ix_llm_jobs_rq_job_id", "llm_jobs", ["rq_job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_jobs_rq_job_id", table_name="llm_jobs")
    op.drop_column("llm_jobs", "rq_job_id")
