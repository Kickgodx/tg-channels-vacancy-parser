"""llm_jobs: ручной импорт вакансий как LLM-джоба.

У джобы импорта нет резюме и вакансии — ``resume_id`` / ``vacancy_id`` становятся
nullable; ``result`` — поэлементный итог импорта, пишется по мере обработки.

Revision ID: 023_llm_jobs_import
Revises: 022_hh_sync_jobs
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023_llm_jobs_import"
down_revision: Union[str, None] = "022_hh_sync_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже добавить колонку.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("llm_jobs")}
    with op.batch_alter_table("llm_jobs") as batch:
        batch.alter_column("resume_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("vacancy_id", existing_type=sa.String(length=128), nullable=True)
        if "result" not in columns:
            batch.add_column(sa.Column("result", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM llm_jobs WHERE resume_id IS NULL OR vacancy_id IS NULL")
    with op.batch_alter_table("llm_jobs") as batch:
        batch.drop_column("result")
        batch.alter_column("vacancy_id", existing_type=sa.String(length=128), nullable=False)
        batch.alter_column("resume_id", existing_type=sa.Integer(), nullable=False)
