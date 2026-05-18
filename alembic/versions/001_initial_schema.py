"""Начальная схема: parse_runs, vacancies, ai_extraction_cache.

Revision ID: 001_initial
Revises:
Create Date: 2026-05-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parse_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("run_params", sa.JSON(), nullable=True),
        sa.Column("posts_fetched", sa.Integer(), nullable=False),
        sa.Column("parse_warnings", sa.Text(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parse_runs_status", "parse_runs", ["status"], unique=False)

    op.create_table(
        "vacancies",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("source_post_key", sa.String(length=128), nullable=False),
        sa.Column("source_channel", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("post_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("cleaned_text", sa.Text(), nullable=False),
        sa.Column("detected_specializations", sa.JSON(), nullable=False),
        sa.Column("detected_grade", sa.String(length=32), nullable=False),
        sa.Column("detected_salary_min", sa.Float(), nullable=True),
        sa.Column("detected_salary_max", sa.Float(), nullable=True),
        sa.Column("detected_salary_avg", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(length=16), nullable=True),
        sa.Column("work_format", sa.String(length=32), nullable=True),
        sa.Column("country", sa.String(length=256), nullable=True),
        sa.Column("city", sa.String(length=256), nullable=True),
        sa.Column("company", sa.String(length=256), nullable=True),
        sa.Column("contact", sa.String(length=512), nullable=True),
        sa.Column("technologies", sa.JSON(), nullable=False),
        sa.Column("required_experience", sa.Text(), nullable=True),
        sa.Column("employment_type", sa.String(length=256), nullable=True),
        sa.Column("application_urls", sa.JSON(), nullable=False),
        sa.Column("extraction_source", sa.String(length=16), nullable=False),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column("vacancy_score", sa.Integer(), nullable=False),
        sa.Column("vacancy_substantive_score", sa.Integer(), nullable=False),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False),
        sa.Column("duplicate_group_id", sa.String(length=128), nullable=True),
        sa.Column("duplicate_reason", sa.Text(), nullable=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_parse_run_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vacancies_source_post_key", "vacancies", ["source_post_key"], unique=False)
    op.create_index("ix_vacancies_source_channel", "vacancies", ["source_channel"], unique=False)
    op.create_index("ix_vacancies_post_date", "vacancies", ["post_date"], unique=False)
    op.create_index("ix_vacancies_detected_grade", "vacancies", ["detected_grade"], unique=False)
    op.create_index("ix_vacancies_work_format", "vacancies", ["work_format"], unique=False)
    op.create_index("ix_vacancies_last_parse_run_id", "vacancies", ["last_parse_run_id"], unique=False)

    op.create_table(
        "ai_extraction_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("cache_key"),
    )
    op.create_index(
        "ix_ai_extraction_cache_expires_at",
        "ai_extraction_cache",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_extraction_cache_expires_at", table_name="ai_extraction_cache")
    op.drop_table("ai_extraction_cache")
    op.drop_index("ix_vacancies_last_parse_run_id", table_name="vacancies")
    op.drop_index("ix_vacancies_work_format", table_name="vacancies")
    op.drop_index("ix_vacancies_detected_grade", table_name="vacancies")
    op.drop_index("ix_vacancies_post_date", table_name="vacancies")
    op.drop_index("ix_vacancies_source_post_key", table_name="vacancies")
    op.drop_index("ix_vacancies_source_channel", table_name="vacancies")
    op.drop_table("vacancies")
    op.drop_index("ix_parse_runs_status", table_name="parse_runs")
    op.drop_table("parse_runs")
