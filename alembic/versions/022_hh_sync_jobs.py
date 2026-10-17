"""hh_sync_jobs: HH sync как durable-джоба (lease, attempts, recover) вместо синхронного запроса.

Revision ID: 022_hh_sync_jobs
Revises: 021_user_token_version
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "022_hh_sync_jobs"
down_revision: Union[str, None] = "021_user_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "hh_sync_jobs"


def upgrade() -> None:
    bind = op.get_bind()
    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if not sa.inspect(bind).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("progress", sa.JSON(), nullable=True),
            sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("requested_by", sa.Integer(), nullable=True),
            sa.Column("rq_job_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_owner", sa.String(length=64), nullable=True),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(
                ["requested_by"], ["users.id"], name="fk_hh_sync_jobs_requested_by", ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id", name="pk_hh_sync_jobs"),
        )
        op.create_index("ix_hh_sync_jobs_status", _TABLE, ["status"])
        op.create_index("ix_hh_sync_jobs_requested_by", _TABLE, ["requested_by"])
        op.create_index("ix_hh_sync_jobs_rq_job_id", _TABLE, ["rq_job_id"])
    # Один активный sync: partial UNIQUE по ((true)) — см. 015.
    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_hh_sync_jobs_one_active "
            f"ON {_TABLE} ((true)) WHERE status IN ('queued', 'running')"
        )
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.execute(sa.text("DROP INDEX IF EXISTS uq_hh_sync_jobs_one_active"))
        op.drop_table(_TABLE)
