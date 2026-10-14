"""Чекпоинты инкрементальной вычитки Telegram-каналов.

Revision ID: 017_channel_checkpoints
Revises: 016_job_attempts
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017_channel_checkpoints"
down_revision: Union[str, None] = "016_job_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "telegram_channel_checkpoints"


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("channel", sa.String(length=128), primary_key=True),
        sa.Column("last_message_id", sa.BigInteger(), nullable=False),
        sa.Column("covered_from", sa.Date(), nullable=False),
        sa.Column("parse_run_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
