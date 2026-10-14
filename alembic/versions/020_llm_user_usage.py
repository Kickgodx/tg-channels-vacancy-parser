"""Суточный учёт вызовов LLM на пользователя (квота LLM_USER_DAILY_CALLS).

Revision ID: 020_llm_user_usage
Revises: 019_specializations_jsonb_gin
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "020_llm_user_usage"
down_revision: Union[str, None] = "019_specializations_jsonb_gin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "llm_user_usage"


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_llm_user_usage_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "day", name="pk_llm_user_usage"),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
