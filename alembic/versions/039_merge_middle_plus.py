"""Грейд ``middle+`` сливается с ``middle``.

``middle+`` ставила только LLM и только дробил аналитику по грейдам; теперь такого
грейда нет (``tg_jobs_parser/extraction/grade_extractor.py``), как и ``senior+``
(миграция 035). Меняются вакансии и сохранённые фильтры списка. Снимки метрик прошлых
прогонов (``parse_runs.metrics``) — история, их не трогаем. Downgrade не разделяет
грейды обратно.

Revision ID: 039_merge_middle_plus
Revises: 038_collapse_1c_technologies
Create Date: 2026-10-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "039_merge_middle_plus"
down_revision: Union[str, None] = "038_collapse_1c_technologies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE vacancies SET detected_grade = 'middle' WHERE detected_grade = 'middle+'")
    op.execute(
        "UPDATE saved_filters SET params = jsonb_set(params::jsonb, '{grade}', '\"middle\"')::json "
        "WHERE params::jsonb ->> 'grade' = 'middle+'"
    )


def downgrade() -> None:
    pass
