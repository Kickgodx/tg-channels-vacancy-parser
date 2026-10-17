"""parse_runs: убрать статус ``pending``.

``pending`` остался от синхронной схемы запуска: с durable-прогонами (014) строка
создаётся сразу в ``queued``. Бэкенд не считал ``pending`` активным (нет в
``uq_parse_runs_one_active`` и recover), а UI считал — такая строка (например, из
старого дампа) висела бы «в процессе» вечно. Закрываем её как ``failed``.

Revision ID: 029_parse_runs_drop_pending
Revises: 028_llm_jobs_one_active
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "029_parse_runs_drop_pending"
down_revision: Union[str, None] = "028_llm_jobs_one_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE parse_runs
               SET status = 'failed',
                   error_message = COALESCE(error_message, 'Прогон в устаревшем статусе pending не был запущен'),
                   finished_at = COALESCE(finished_at, now())
             WHERE status = 'pending'
            """
        )
    )


def downgrade() -> None:
    # Исходный статус не восстановить: строки уже закрыты как failed.
    pass
