"""llm_jobs: одна активная джоба на (пользователь, вид, резюме, вакансия).

Постановка матча / письма проверяла «нет ли активной» и вставляла строку без
ограничения в БД: два параллельных запроса создавали две джобы. Partial UNIQUE —
по образцу ``uq_parse_runs_one_active``. Импорт исключён: у него нет пары
резюме–вакансия, повтор ловит Idempotency-Key.

Revision ID: 028_llm_jobs_one_active
Revises: 027_vacancy_sightings
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028_llm_jobs_one_active"
down_revision: Union[str, None] = "027_vacancy_sightings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE = "status IN ('queued', 'running') AND kind <> 'import'"


def upgrade() -> None:
    # Дубли, успевшие появиться до индекса: оставляем одну джобу на пару
    # (выполняющуюся, иначе самую новую), остальные закрываем ошибкой.
    op.execute(
        sa.text(
            f"""
            UPDATE llm_jobs
               SET status = 'error',
                   error_message = 'Дубликат активной задачи',
                   finished_at = now(),
                   lease_owner = NULL,
                   lease_until = NULL
             WHERE id IN (
                SELECT id FROM (
                    SELECT id, row_number() OVER (
                        PARTITION BY user_id, kind, resume_id, vacancy_id
                        ORDER BY (status = 'running') DESC, created_at DESC, id
                    ) AS rn
                      FROM llm_jobs
                     WHERE {_ACTIVE}
                ) ranked
                 WHERE rn > 1
             )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_jobs_one_active_pair "
            f"ON llm_jobs (user_id, kind, resume_id, vacancy_id) WHERE {_ACTIVE}"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_llm_jobs_one_active_pair"))
