"""SQL-выражения по эмбеддингам вакансий: расстояние до вектора и окно смыслового поиска.

Сравниваются только векторы модели ``model`` — той, которой посчитан ``vector``: пока каталог
переиндексируется новой моделью, векторы прежней ведут себя как отсутствующие.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.db.models import VacancyEmbeddingRow
from backend.db.vector_type import query_vector

# Смысловой поиск: сколько ближайших к запросу вакансий (под остальными фильтрами) считать
# совпадениями. Дальше по смыслу — уже шум, а не «чуть менее подходящие».
SEMANTIC_WINDOW = 300


def vacancy_distance(vacancy_id_column, vector: list[float], *, model: str):
    """Скалярный подзапрос: косинусное расстояние вектора вакансии до ``vector`` (NULL — нет вектора ``model``)."""
    e = VacancyEmbeddingRow
    return (
        select(e.embedding.cosine_distance(query_vector(vector)))
        .where(e.vacancy_id == vacancy_id_column, e.model == model)
        .scalar_subquery()
    )


def semantic_window_ids(
    vacancy_alias, vector: list[float], conditions: list, *, model: str, limit: int = SEMANTIC_WINDOW
):
    """id ближайших к ``vector`` вакансий из ``vacancy_alias`` под ``conditions`` (векторы ``model``)."""
    e = VacancyEmbeddingRow.__table__.alias("semantic_emb")
    distance = e.c.embedding.cosine_distance(query_vector(vector))
    return (
        select(vacancy_alias.c.id)
        .select_from(vacancy_alias.join(e, (e.c.vacancy_id == vacancy_alias.c.id) & (e.c.model == model)))
        .where(*conditions)
        .order_by(distance, vacancy_alias.c.id)
        .limit(limit)
    )
