"""users.token_version: отзыв выданных JWT при смене пароля и «выйти везде».

Revision ID: 021_user_token_version
Revises: 020_llm_user_usage
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "021_user_token_version"
down_revision: Union[str, None] = "020_llm_user_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    return any(c["name"] == "token_version" for c in sa.inspect(op.get_bind()).get_columns("users"))


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать колонку.
    if _has_column():
        return
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    if _has_column():
        with op.batch_alter_table("users") as batch:
            batch.drop_column("token_version")
