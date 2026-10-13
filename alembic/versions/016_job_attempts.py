"""Счётчик попыток у parse_runs и llm_jobs (лимит requeue после потери lease).

Revision ID: 016_job_attempts
Revises: 015_p0_leases_idempotency
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016_job_attempts"
down_revision: Union[str, None] = "015_p0_leases_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("parse_runs", "llm_jobs")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in _TABLES:
        # create_all fallback после падения прошлой попытки мог уже создать колонку.
        if "attempts" not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in _TABLES:
        if "attempts" in {c["name"] for c in insp.get_columns(table)}:
            op.drop_column(table, "attempts")
