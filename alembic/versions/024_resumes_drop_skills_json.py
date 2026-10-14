"""resumes: удалить неиспользуемую колонку ``skills_json``.

Колонку никто не заполнял (всегда NULL) и не читал.

Revision ID: 024_resumes_drop_skills_json
Revises: 023_llm_jobs_import
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "024_resumes_drop_skills_json"
down_revision: Union[str, None] = "023_llm_jobs_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_all fallback по актуальной модели создаёт таблицу уже без колонки.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("resumes")}
    if "skills_json" not in columns:
        return
    with op.batch_alter_table("resumes") as batch:
        batch.drop_column("skills_json")


def downgrade() -> None:
    with op.batch_alter_table("resumes") as batch:
        batch.add_column(sa.Column("skills_json", sa.JSON(), nullable=True))
