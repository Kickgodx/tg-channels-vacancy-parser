"""Durable parse jobs: rq_job_id, progress, cancel_requested.

Revision ID: 014_parse_runs_durable_jobs
Revises: 013_vac_active_post_date_idx
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014_parse_runs_durable_jobs"
down_revision: Union[str, None] = "013_vac_active_post_date_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("parse_runs", sa.Column("rq_job_id", sa.String(length=64), nullable=True))
    op.add_column("parse_runs", sa.Column("progress", sa.JSON(), nullable=True))
    op.add_column(
        "parse_runs",
        sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "parse_runs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_parse_runs_rq_job_id", "parse_runs", ["rq_job_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_parse_runs_rq_job_id", table_name="parse_runs")
    op.drop_column("parse_runs", "cancel_requested")
    op.drop_column("parse_runs", "progress_updated_at")
    op.drop_column("parse_runs", "progress")
    op.drop_column("parse_runs", "rq_job_id")
