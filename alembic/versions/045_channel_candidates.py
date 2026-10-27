"""Кандидаты в источники: каналы, из которых пересылают посты в наши каналы.

``channel_candidates`` — канал Telegram по id (username и название — последние увиденные,
``status`` — new | ignored). ``telegram_forwards`` — пересланный пост нашего канала (PK —
канал + id поста: повторное чтение того же окна не раздувает счётчики), откуда переслан и
стал ли кандидатом в вакансии.

Revision ID: 045_channel_candidates
Revises: 044_market_digests
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_channel_candidates"
down_revision: Union[str, None] = "044_market_digests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_candidates",
        sa.Column("channel_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("channel_id", name="pk_channel_candidates"),
    )
    op.create_table(
        "telegram_forwards",
        sa.Column("channel", sa.String(length=128), nullable=False),
        sa.Column("post_id", sa.String(length=64), nullable=False),
        sa.Column("source_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("source_post_id", sa.BigInteger(), nullable=True),
        sa.Column("post_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_vacancy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("channel", "post_id", name="pk_telegram_forwards"),
        sa.ForeignKeyConstraint(
            ["source_channel_id"],
            ["channel_candidates.channel_id"],
            ondelete="CASCADE",
            name="fk_telegram_forwards_source_channel_id",
        ),
    )
    op.create_index("ix_telegram_forwards_source_channel_id", "telegram_forwards", ["source_channel_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_forwards_source_channel_id", table_name="telegram_forwards")
    op.drop_table("telegram_forwards")
    op.drop_table("channel_candidates")
