"""Грейд ``senior+`` сливается с ``senior``.

``senior+`` почти целиком был «HH, опыт больше 6 лет» и не отличался от ``senior``;
теперь такого грейда нет (``tg_jobs_parser/extraction/grade_extractor.py``). Меняются
вакансии и сохранённые фильтры списка. Снимки метрик прошлых прогонов
(``parse_runs.metrics``) — история, их не трогаем. Downgrade не разделяет грейды обратно.

Revision ID: 035_merge_senior_plus
Revises: 034_vacancies_sort_indexes
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "035_merge_senior_plus"
down_revision: Union[str, None] = "034_vacancies_sort_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE vacancies SET detected_grade = 'senior' WHERE detected_grade = 'senior+'")
    op.execute(
        "UPDATE saved_filters SET params = jsonb_set(params::jsonb, '{grade}', '\"senior\"')::json "
        "WHERE params::jsonb ->> 'grade' = 'senior+'"
    )


def downgrade() -> None:
    pass
