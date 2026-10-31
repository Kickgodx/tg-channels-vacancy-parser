"""maintenance_requests: запросы фоновой работы вне расписания (пересчёт карты рынка).

Кнопка администратора не считает карту в запросе API, а оставляет здесь запрос: его выполняет
фоновое обслуживание (лидер API или ``jobdesk-maintenance-worker``, см. ``backend/maintenance.py``).
Одна строка на задачу: повторное нажатие лишь обновляет время запроса.

Revision ID: 051_maintenance_requests
Revises: 050_salary_estimates
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "051_maintenance_requests"
down_revision: Union[str, None] = "050_salary_estimates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "maintenance_requests"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("task", name="pk_maintenance_requests"),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
