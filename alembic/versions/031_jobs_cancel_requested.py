"""llm_jobs / hh_sync_jobs: отмена (``cancel_requested`` + статус ``cancelled``).

Как у ``parse_runs``: флаг в БД переживает рестарт API и воркера; ``queued``
отменяется сразу, ``running`` — флагом, который проверяет воркер. CHECK на статус
нет, поэтому ``cancelled`` новой схемы не требует.

Revision ID: 031_jobs_cancel_requested
Revises: 030_llm_results_requested_model
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "031_jobs_cancel_requested"
down_revision: Union[str, None] = "030_llm_results_requested_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("llm_jobs", "hh_sync_jobs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    # Старый код не знает ``cancelled``: закрываем такие задачи его терминальными статусами.
    op.execute(
        sa.text(
            "UPDATE llm_jobs SET status = 'error', error_message = COALESCE(error_message, 'Задача отменена')"
            " WHERE status = 'cancelled'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE hh_sync_jobs SET status = 'failed', error_message = COALESCE(error_message, 'Sync отменён')"
            " WHERE status = 'cancelled'"
        )
    )
    for table in _TABLES:
        op.drop_column(table, "cancel_requested")
