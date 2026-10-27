"""Ссылка скрытой копии на исходную вакансию: ``vacancies.duplicate_of``.

Разовое склеивание почти-дублей (``db merge-duplicates``) помечает более позднюю копию
``is_duplicate`` и ставит ссылку на исходную вакансию. Строка остаётся: на неё могут
ссылаться оценки и письма пользователей.

Revision ID: 046_vacancy_duplicate_of
Revises: 045_channel_candidates
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "046_vacancy_duplicate_of"
down_revision: Union[str, None] = "045_channel_candidates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vacancies", sa.Column("duplicate_of", sa.String(length=128), nullable=True))
    op.create_foreign_key(
        "fk_vacancies_duplicate_of", "vacancies", "vacancies", ["duplicate_of"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_vacancies_duplicate_of", "vacancies", ["duplicate_of"])


def downgrade() -> None:
    op.drop_index("ix_vacancies_duplicate_of", table_name="vacancies")
    op.drop_constraint("fk_vacancies_duplicate_of", "vacancies", type_="foreignkey")
    op.drop_column("vacancies", "duplicate_of")
