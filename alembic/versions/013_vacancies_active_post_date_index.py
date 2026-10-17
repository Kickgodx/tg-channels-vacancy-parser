"""Частичный индекс активных вакансий для списка (post_date, id).

Revision ID: 013_vac_active_post_date_idx
Revises: 012_llm_jobs_rq_job_id
Create Date: 2026-10-10

Id ≤32 chars: колонка alembic_version.version_num по умолчанию VARCHAR(32).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013_vac_active_post_date_idx"
down_revision: Union[str, None] = "012_llm_jobs_rq_job_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Совпадает с ORDER BY post_date DESC NULLS LAST, id — Index Scan + early stop на LIMIT.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_vacancies_active_post_date "
            "ON vacancies (post_date DESC NULLS LAST, id ASC) "
            "WHERE is_duplicate IS false AND archived_at IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_vacancies_active_post_date", table_name="vacancies")
