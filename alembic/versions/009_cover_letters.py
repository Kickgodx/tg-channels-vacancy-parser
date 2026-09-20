"""Сопроводительные письма: LLM-черновики откликов резюме → вакансия.

``cover_letters`` — PK (resume_id, vacancy_id): строка является и кэшем
генерации (повторное открытие отдаёт сохранённый текст без вызова LLM);
перегенерация перезаписывает строку. ``tone`` фиксирует выбранный тон,
``model`` — модель, сгенерировавшую текст.

Revision ID: 009_cover_letters
Revises: 008_match_breakdown
Create Date: 2026-09-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009_cover_letters"
down_revision: Union[str, None] = "008_match_breakdown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cover_letters",
        sa.Column(
            "resume_id",
            sa.Integer(),
            sa.ForeignKey("resumes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "vacancy_id",
            sa.String(length=128),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_cover_letters_vacancy_id",
        "cover_letters",
        ["vacancy_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_cover_letters_vacancy_id", table_name="cover_letters")
    op.drop_table("cover_letters")
