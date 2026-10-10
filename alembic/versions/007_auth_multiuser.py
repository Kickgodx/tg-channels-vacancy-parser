"""Аутентификация и per-user данные: users + владельцы резюме/пресетов.

``resumes.user_id`` и ``saved_filters.user_id`` остаются nullable: строки,
созданные до появления пользователей, получают владельца при регистрации
первого юзера (claim legacy rows в ``POST /auth/register``).
``vacancy_matches`` наследует владельца через ``resumes`` — отдельной колонки
не требуется.

Revision ID: 007_auth_multiuser
Revises: 006_resume_matching
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007_auth_multiuser"
down_revision: Union[str, None] = "006_resume_matching"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # batch-режим — SQLite не умеет ALTER TABLE ADD COLUMN с FK.
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_resumes_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_resumes_user_id", ["user_id"], unique=False)

    with op.batch_alter_table("saved_filters") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_saved_filters_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_saved_filters_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("saved_filters") as batch:
        batch.drop_index("ix_saved_filters_user_id")
        batch.drop_column("user_id")
    with op.batch_alter_table("resumes") as batch:
        batch.drop_index("ix_resumes_user_id")
        batch.drop_column("user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
