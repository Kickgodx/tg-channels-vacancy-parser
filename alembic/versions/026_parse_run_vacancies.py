"""parse_run_vacancies: какие вакансии встретил каждый прогон.

``vacancies.last_parse_run_id`` перезаписывается следующим прогоном, поэтому история
и экспорт старых прогонов пустели. Существующие привязки переносятся из него — это
всё, что о прошлых прогонах ещё известно.

Revision ID: 026_parse_run_vacancies
Revises: 025_refresh_tokens
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "026_parse_run_vacancies"
down_revision: Union[str, None] = "025_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "parse_run_vacancies"


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("parse_run_id", sa.Uuid(), nullable=False),
            sa.Column("vacancy_id", sa.String(length=128), nullable=False),
            sa.ForeignKeyConstraint(
                ["parse_run_id"],
                ["parse_runs.id"],
                name="fk_parse_run_vacancies_parse_run_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["vacancy_id"],
                ["vacancies.id"],
                name="fk_parse_run_vacancies_vacancy_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("parse_run_id", "vacancy_id", name="pk_parse_run_vacancies"),
        )
        op.create_index("ix_parse_run_vacancies_vacancy_id", _TABLE, ["vacancy_id"])
    # JOIN с parse_runs: last_parse_run_id — не FK, удалённые прогоны в нём остаются.
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_TABLE} (parse_run_id, vacancy_id)
            SELECT v.last_parse_run_id, v.id
            FROM vacancies v
            JOIN parse_runs r ON r.id = v.last_parse_run_id
            WHERE NOT EXISTS (
                SELECT 1 FROM {_TABLE} l
                WHERE l.parse_run_id = v.last_parse_run_id AND l.vacancy_id = v.id
            )
            """
        )
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
