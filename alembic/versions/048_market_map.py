"""Карта рынка: ``market_maps`` (последний расчёт и кластеры) и ``market_map_points``.

Координаты вакансий на 2D-карте (t-SNE по эмбеддингам) и номер кластера. Хранится только
последняя карта: новый расчёт удаляет прежние (точки — каскадом).

Revision ID: 048_market_map
Revises: 047_sighting_changes
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "048_market_map"
down_revision: Union[str, None] = "047_sighting_changes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_maps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("params", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("clusters", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_table(
        "market_map_points",
        sa.Column(
            "map_id",
            sa.Integer(),
            sa.ForeignKey("market_maps.id", ondelete="CASCADE", name="fk_market_map_points_map_id"),
            primary_key=True,
        ),
        sa.Column(
            "vacancy_id",
            sa.String(128),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE", name="fk_market_map_points_vacancy_id"),
            primary_key=True,
        ),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("cluster", sa.Integer(), nullable=False),
    )
    op.create_index("ix_market_map_points_vacancy_id", "market_map_points", ["vacancy_id"])


def downgrade() -> None:
    op.drop_index("ix_market_map_points_vacancy_id", table_name="market_map_points")
    op.drop_table("market_map_points")
    op.drop_table("market_maps")
