"""Кэш короткой выдачи HH для skip detail при повторных sync.

Revision ID: 010_hh_listings
Revises: 009_cover_letters
Create Date: 2026-10-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_hh_listings"
down_revision: Union[str, None] = "009_cover_letters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hh_listings",
        sa.Column("hh_id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("employer", sa.String(256), nullable=True),
        sa.Column("area", sa.String(256), nullable=True),
        sa.Column("alternate_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("salary_json", sa.JSON(), nullable=True),
        sa.Column("snippet_json", sa.JSON(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("detail_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_hh_listings_last_seen_at", "hh_listings", ["last_seen_at"])
    op.create_index("ix_hh_listings_detail_fetched_at", "hh_listings", ["detail_fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_hh_listings_detail_fetched_at", table_name="hh_listings")
    op.drop_index("ix_hh_listings_last_seen_at", table_name="hh_listings")
    op.drop_table("hh_listings")
