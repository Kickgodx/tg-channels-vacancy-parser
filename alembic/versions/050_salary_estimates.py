"""vacancy_salary_estimates: ориентировочная зарплата вакансии без вилки.

Оценка — квантили рублёвой зарплаты ближайших по эмбеддингу вакансий с зарплатой того же
грейда за год до поста (``backend/services/salary_estimate_service.py``). Отдельная таблица:
это производные данные, их пересчитывает лидер API, и они не должны трогать ``vacancies``
(``updated_at``, аналитика).

Revision ID: 050_salary_estimates
Revises: 049_api_tokens
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "050_salary_estimates"
down_revision: Union[str, None] = "049_api_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "vacancy_salary_estimates"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("vacancy_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=True),
        sa.Column("neighbours", sa.Integer(), nullable=False),
        sa.Column("p25", sa.Float(), nullable=True),
        sa.Column("median", sa.Float(), nullable=True),
        sa.Column("p75", sa.Float(), nullable=True),
        sa.Column("neighbour_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancies.id"], name="fk_vacancy_salary_estimates_vacancy_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("vacancy_id", name="pk_vacancy_salary_estimates"),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
