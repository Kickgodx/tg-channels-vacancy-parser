"""vacancies.search_tsv: хранимый tsvector для полнотекстового поиска.

Индекс по выражению ``to_tsvector('russian', cleaned_text)`` ускорял только WHERE:
``ts_rank`` в ORDER BY пересчитывал tsvector по каждому совпадению (45 тыс.
вакансий, частое слово — ~1.4 с на запрос). Generated stored-колонка считается при
записи, ранжирование читает готовое значение. ``ADD COLUMN ... STORED`` переписывает
таблицу под ACCESS EXCLUSIVE — на текущих объёмах это доли секунды.

Revision ID: 033_vacancies_search_tsv
Revises: 032_llm_jobs_quota_day
Create Date: 2026-10-19
"""

from typing import Sequence, Union

from alembic import op

revision: str = "033_vacancies_search_tsv"
down_revision: Union[str, None] = "032_llm_jobs_quota_day"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE vacancies ADD COLUMN search_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('russian', cleaned_text)) STORED"
    )
    op.execute("CREATE INDEX ix_vacancies_search_tsv ON vacancies USING GIN (search_tsv)")
    op.execute("DROP INDEX IF EXISTS ix_vacancies_fts")


def downgrade() -> None:
    op.execute("CREATE INDEX ix_vacancies_fts ON vacancies USING GIN (to_tsvector('russian', cleaned_text))")
    op.execute("DROP INDEX IF EXISTS ix_vacancies_search_tsv")
    op.execute("ALTER TABLE vacancies DROP COLUMN search_tsv")
