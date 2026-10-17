"""JSON в БД пишется UTF-8 без \\uXXXX-эскейпов."""

from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, MetaData, Table, text

from backend.db.session import get_engine


def test_json_columns_store_cyrillic_without_unicode_escapes(pg_db):
    t = Table("t", MetaData(), Column("id", Integer, primary_key=True), Column("j", JSON), prefixes=["TEMPORARY"])

    payload = {"requirement": "Хорошо понимаешь HTTP"}
    with get_engine().begin() as conn:
        t.create(conn)
        conn.execute(t.insert().values(id=1, j=payload))
        raw = conn.execute(text("SELECT j::text FROM t")).scalar_one()

    assert "Хорошо" in raw
    assert "\\u" not in raw
