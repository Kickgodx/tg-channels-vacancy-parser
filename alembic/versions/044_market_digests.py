"""Еженедельный обзор рынка: факты недели и текст LLM по ним.

``market_digests`` — одна строка на неделю (``period_start`` — понедельник, UTC). ``facts`` —
посчитанные SQL-агрегаты, ``summary`` — текст LLM (может отсутствовать: LLM выключен или
недоступен, тогда причина — в ``summary_error``).

Revision ID: 044_market_digests
Revises: 043_interview_preps
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "044_market_digests"
down_revision: Union[str, None] = "043_interview_preps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_digests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_error", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("requested_model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_market_digests"),
        sa.UniqueConstraint("period_start", name="uq_market_digests_period_start"),
    )


def downgrade() -> None:
    op.drop_table("market_digests")
