"""Эмбеддинги вакансий и резюме (pgvector): похожие вакансии, подбор под резюме, смысловой поиск.

Отдельные таблицы, а не колонка в ``vacancies``: вектор 1536 float — 6 КБ на строку, которые
не нужны ни списку, ни аналитике. ``content_hash`` — хэш текста, из которого посчитан вектор:
изменился текст или модель — вектор пересчитывается. HNSW по косинусному расстоянию.

Нужен Postgres с расширением vector (образ docker/postgres). ``CREATE EXTENSION`` требует прав
суперпользователя или владельца базы с trusted-расширением.

Revision ID: 042_embeddings
Revises: 041_resume_tailorings
Create Date: 2026-10-27
"""

from typing import Sequence, Union

from alembic import op

revision: str = "042_embeddings"
down_revision: Union[str, None] = "041_resume_tailorings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE vacancy_embeddings (
            vacancy_id VARCHAR(128) NOT NULL,
            model VARCHAR(128) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_vacancy_embeddings PRIMARY KEY (vacancy_id),
            CONSTRAINT fk_vacancy_embeddings_vacancy_id FOREIGN KEY (vacancy_id)
                REFERENCES vacancies (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_vacancy_embeddings_hnsw ON vacancy_embeddings USING hnsw (embedding vector_cosine_ops)")
    op.execute(
        """
        CREATE TABLE resume_embeddings (
            resume_id INTEGER NOT NULL,
            model VARCHAR(128) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_resume_embeddings PRIMARY KEY (resume_id),
            CONSTRAINT fk_resume_embeddings_resume_id FOREIGN KEY (resume_id)
                REFERENCES resumes (id) ON DELETE CASCADE
        )
        """
    )


def downgrade() -> None:
    op.drop_table("resume_embeddings")
    op.drop_table("vacancy_embeddings")
