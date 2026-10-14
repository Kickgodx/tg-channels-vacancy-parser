"""vacancies.detected_specializations: JSON → JSONB + GIN-индекс (только Postgres).

Фильтр по специализации (``@>`` в ``vacancy_filters``) приводил JSON к JSONB в
каждом запросе — индекс по такому выражению невозможен, всегда seq scan.

Revision ID: 019_specializations_jsonb_gin
Revises: 018_llm_jobs_fk_indexes
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "019_specializations_jsonb_gin"
down_revision: Union[str, None] = "018_llm_jobs_fk_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_vacancies_specializations_gin"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite (тесты): JSON как есть, фильтр идёт через json_each
    columns = {c["name"]: c["type"] for c in sa.inspect(bind).get_columns("vacancies")}
    if not isinstance(columns["detected_specializations"], JSONB):
        op.execute(
            "ALTER TABLE vacancies ALTER COLUMN detected_specializations "
            "TYPE jsonb USING detected_specializations::jsonb"
        )
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON vacancies USING gin (detected_specializations)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute(
        "ALTER TABLE vacancies ALTER COLUMN detected_specializations TYPE json USING detected_specializations::json"
    )
