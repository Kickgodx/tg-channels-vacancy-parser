"""Индексы на FK llm_jobs.resume_id / vacancy_id.

Без них каскадное удаление резюме (и вакансии) сканирует llm_jobs целиком.

Revision ID: 018_llm_jobs_fk_indexes
Revises: 017_channel_checkpoints
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018_llm_jobs_fk_indexes"
down_revision: Union[str, None] = "017_channel_checkpoints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = (
    ("ix_llm_jobs_resume_id", "resume_id"),
    ("ix_llm_jobs_vacancy_id", "vacancy_id"),
)


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать индексы.
    existing = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("llm_jobs")}
    for name, column in _INDEXES:
        if name not in existing:
            op.create_index(name, "llm_jobs", [column])


def downgrade() -> None:
    existing = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("llm_jobs")}
    for name, _ in _INDEXES:
        if name in existing:
            op.drop_index(name, table_name="llm_jobs")
