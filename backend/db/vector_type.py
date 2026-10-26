"""Тип ``vector(N)`` pgvector для SQLAlchemy без пакета ``pgvector`` (он тянет numpy).

Значение передаётся текстом ``[x1,x2,…]`` с явным ``CAST(… AS vector)``; косинусное
расстояние — оператор ``<=>`` (0 — одинаковое направление, 2 — противоположное).
"""

from __future__ import annotations

from sqlalchemy import Float, cast
from sqlalchemy.types import UserDefinedType

EMBEDDING_DIMENSIONS = 1536


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


def parse_vector(value: str | None) -> list[float] | None:
    if value is None:
        return None
    text = value.strip()[1:-1]
    return [float(x) for x in text.split(",")] if text else []


class _VectorSpec(UserDefinedType):
    """Только имя типа для ``CAST(… AS vector(N))`` — без своих bind-обработчиков."""

    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw) -> str:
        return f"vector({self.dimensions})"


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **kw) -> str:
        return f"vector({self.dimensions})"

    def bind_processor(self, dialect):
        def process(value):
            return None if value is None else vector_literal(value)

        return process

    def bind_expression(self, bindvalue):
        return cast(bindvalue, _VectorSpec(self.dimensions))

    def result_processor(self, dialect, coltype):
        return parse_vector

    class comparator_factory(UserDefinedType.Comparator):
        def cosine_distance(self, other):
            return self.op("<=>", return_type=Float)(other)


def query_vector(values: list[float]):
    """Литерал запроса для ``<=>``: ``CAST('[…]' AS vector)`` с привязанным параметром."""
    from sqlalchemy import bindparam

    return bindparam(None, values, type_=Vector(len(values)))
