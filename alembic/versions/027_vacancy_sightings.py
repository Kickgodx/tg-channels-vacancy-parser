"""vacancy_sightings: каждое появление вакансии в посте Telegram, включая дубли.

В ``vacancies`` пишется только первый пост с данным текстом, поэтому дубли
каналов нигде не оставались и ``unique_share`` в качестве источников был всегда 1.0.
Существующие вакансии Telegram переносятся как оригиналы — о дублях прошлых
прогонов данных нет.

Revision ID: 027_vacancy_sightings
Revises: 026_parse_run_vacancies
Create Date: 2026-10-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "027_vacancy_sightings"
down_revision: Union[str, None] = "026_parse_run_vacancies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "vacancy_sightings"


def upgrade() -> None:
    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if not sa.inspect(op.get_bind()).has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("source_post_key", sa.String(length=128), nullable=False),
            sa.Column("vacancy_id", sa.String(length=128), nullable=False),
            sa.Column("source_channel", sa.String(length=128), nullable=False),
            sa.Column("post_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("parse_run_id", sa.Uuid(), nullable=True),
            sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("duplicate_reason", sa.String(length=64), nullable=True),
            sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["vacancy_id"],
                ["vacancies.id"],
                name="fk_vacancy_sightings_vacancy_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["parse_run_id"],
                ["parse_runs.id"],
                name="fk_vacancy_sightings_parse_run_id",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("source_post_key", name="pk_vacancy_sightings"),
        )
        op.create_index("ix_vacancy_sightings_vacancy_id", _TABLE, ["vacancy_id"])
        op.create_index("ix_vacancy_sightings_source_channel", _TABLE, ["source_channel"])
    # HH и ручной импорт — не посты Telegram. LEFT JOIN: last_parse_run_id — не FK,
    # удалённые прогоны в нём остаются. Повтор source_post_key (пост отредактирован —
    # другой текст, другой id) гасит ON CONFLICT.
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_TABLE}
                (source_post_key, vacancy_id, source_channel, post_date, parse_run_id,
                 is_duplicate, duplicate_reason, seen_at)
            SELECT v.source_post_key, v.id, v.source_channel, v.post_date, r.id,
                   :false_, NULL, v.parsed_at
            FROM vacancies v
            LEFT JOIN parse_runs r ON r.id = v.last_parse_run_id
            WHERE v.is_duplicate = :false_ AND v.source_channel NOT IN ('hh', 'manual')
            ON CONFLICT (source_post_key) DO NOTHING
            """
        ).bindparams(sa.bindparam("false_", False, type_=sa.Boolean()))
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(_TABLE):
        op.drop_table(_TABLE)
