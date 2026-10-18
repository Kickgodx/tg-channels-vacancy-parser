"""vacancy_matches / cover_letters: запрошенная модель и версия промпта.

Оценка и письмо — кэш по паре резюме–вакансия, но ``model`` хранит фактически
ответившую модель (может быть fallback). Чтобы понять, устарел ли результат после
смены настроенной модели или bump версии промпта, храним, какую модель запрашивали
и с какой версией промпта. PK не меняется — одна строка на пару.

Существующие строки: ``requested_model = model``, ``prompt_version`` — NULL
(по версии такие строки не устаревают).

Revision ID: 030_llm_results_requested_model
Revises: 029_parse_runs_drop_pending
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030_llm_results_requested_model"
down_revision: Union[str, None] = "029_parse_runs_drop_pending"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("vacancy_matches", "cover_letters")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("requested_model", sa.String(128), nullable=True))
        op.add_column(table, sa.Column("prompt_version", sa.String(32), nullable=True))
        op.execute(sa.text(f"UPDATE {table} SET requested_model = model WHERE requested_model IS NULL"))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "prompt_version")
        op.drop_column(table, "requested_model")
