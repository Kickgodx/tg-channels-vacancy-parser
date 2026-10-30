"""Специализации-синонимы сливаются с каноническими.

``Тестировщик`` и ``Manual QA`` → ``QA``, ``AQA`` → ``Automation QA``,
``Программист, разработчик`` (роль HH) → ``Software Engineer``
(``SPECIALIZATION_ALIASES`` в ``jobdesk/config/schema.py``). Меняются вакансии и
сохранённые фильтры списка; повторы после замены схлопываются, порядок сохраняется.
Снимки прогонов (``parse_runs``) — история, их не трогаем. Downgrade не разделяет обратно.

Revision ID: 036_merge_qa_specializations
Revises: 035_merge_senior_plus
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "036_merge_qa_specializations"
down_revision: Union[str, None] = "035_merge_senior_plus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALIASES = {
    "Manual QA": "QA",
    "Тестировщик": "QA",
    "AQA": "Automation QA",
    "Программист, разработчик": "Software Engineer",
}

_OLD_NAMES = "ARRAY[" + ", ".join(f"'{old}'" for old in _ALIASES) + "]"
_CASE = "CASE e " + " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in _ALIASES.items()) + " ELSE e END"


def _canonical_array(arr: str) -> str:
    """SQL-выражение: JSONB-массив ``arr`` с заменёнными именами, без повторов."""
    return (
        "(SELECT COALESCE(jsonb_agg(name ORDER BY first_ord), '[]'::jsonb) FROM ("
        f" SELECT {_CASE} AS name, min(ord) AS first_ord"
        f" FROM jsonb_array_elements_text({arr}) WITH ORDINALITY AS t(e, ord)"
        f" GROUP BY {_CASE}) d)"
    )


def upgrade() -> None:
    op.execute(
        f"UPDATE vacancies SET detected_specializations = {_canonical_array('detected_specializations')} "
        f"WHERE detected_specializations ?| {_OLD_NAMES}"
    )
    specs = "params::jsonb -> 'specializations'"
    op.execute(
        f"UPDATE saved_filters SET params = jsonb_set(params::jsonb, '{{specializations}}', "
        f"{_canonical_array(specs)})::json "
        f"WHERE jsonb_typeof({specs}) = 'array' AND {specs} ?| {_OLD_NAMES}"
    )


def downgrade() -> None:
    pass
