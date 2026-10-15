"""refresh_tokens: refresh-сессии в httpOnly cookie (ротация, отзыв семьи).

Revision ID: 025_refresh_tokens
Revises: 024_resumes_drop_skills_json
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025_refresh_tokens"
down_revision: Union[str, None] = "024_resumes_drop_skills_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "refresh_tokens"


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=32), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_refresh_tokens_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", _TABLE, ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", _TABLE, ["family_id"])
    op.create_index("ix_refresh_tokens_expires_at", _TABLE, ["expires_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
