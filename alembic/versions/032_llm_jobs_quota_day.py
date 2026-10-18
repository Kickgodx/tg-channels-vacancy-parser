"""llm_jobs.quota_day: UTC-сутки, в которые за задачу списан вызов квоты пользователя.

Пусто — вызов не списывался (администратор, квота 0, импорт) или уже возвращён.
По колонке задача, завершившаяся без ответа модели, возвращает вызов в квоту тех
суток, в которые он списан, и ровно один раз. NULL-колонка без default — только
метаданные, таблица не переписывается.

Revision ID: 032_llm_jobs_quota_day
Revises: 031_jobs_cancel_requested
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "032_llm_jobs_quota_day"
down_revision: Union[str, None] = "031_jobs_cancel_requested"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_jobs", sa.Column("quota_day", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_jobs", "quota_day")
