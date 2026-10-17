"""P0: lease columns, one-active parse index, idempotency_keys.

Revision ID: 015_p0_leases_idempotency
Revises: 014_parse_runs_durable_jobs
Create Date: 2026-10-11

Id ≤32 chars: alembic_version.version_num default VARCHAR(32).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_p0_leases_idempotency"
down_revision: Union[str, None] = "014_parse_runs_durable_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fail_if_multiple_active_parses() -> None:
    """Partial UNIQUE упадёт с неясным IntegrityError — ловим заранее."""
    conn = op.get_bind()
    n = conn.execute(sa.text("SELECT COUNT(*) FROM parse_runs WHERE status IN ('queued', 'running')")).scalar()
    if n is not None and int(n) > 1:
        raise RuntimeError(
            f"Миграция 015: в parse_runs {n} активных прогонов (queued|running). "
            "Оставьте один (или завершите/отмените лишние) и повторите upgrade."
        )


def _create_one_active_parse_index() -> None:
    """Partial UNIQUE по выражению ``((true))``: не больше одной активной строки."""
    # Одинарные скобки (1) — SyntaxError; нужен expression: ((true)).
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_parse_runs_one_active "
            "ON parse_runs ((true)) WHERE status IN ('queued', 'running')"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols_parse = {c["name"] for c in insp.get_columns("parse_runs")}
    cols_llm = {c["name"] for c in insp.get_columns("llm_jobs")}

    if "lease_owner" not in cols_parse:
        op.add_column("parse_runs", sa.Column("lease_owner", sa.String(length=64), nullable=True))
    if "lease_until" not in cols_parse:
        op.add_column("parse_runs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    if "lease_owner" not in cols_llm:
        op.add_column("llm_jobs", sa.Column("lease_owner", sa.String(length=64), nullable=True))
    if "lease_until" not in cols_llm:
        op.add_column("llm_jobs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))

    _fail_if_multiple_active_parses()
    _create_one_active_parse_index()

    # create_all fallback после падения прошлой попытки мог уже создать таблицу.
    if "idempotency_keys" not in insp.get_table_names():
        op.create_table(
            "idempotency_keys",
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("scope", sa.String(length=64), nullable=False),
            sa.Column("actor", sa.String(length=128), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("body", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("key", "scope", "actor"),
        )
        op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "idempotency_keys" in insp.get_table_names():
        op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
        op.drop_table("idempotency_keys")
    op.execute(sa.text("DROP INDEX IF EXISTS uq_parse_runs_one_active"))
    cols_llm = {c["name"] for c in insp.get_columns("llm_jobs")}
    cols_parse = {c["name"] for c in insp.get_columns("parse_runs")}
    if "lease_until" in cols_llm:
        op.drop_column("llm_jobs", "lease_until")
    if "lease_owner" in cols_llm:
        op.drop_column("llm_jobs", "lease_owner")
    if "lease_until" in cols_parse:
        op.drop_column("parse_runs", "lease_until")
    if "lease_owner" in cols_parse:
        op.drop_column("parse_runs", "lease_owner")
