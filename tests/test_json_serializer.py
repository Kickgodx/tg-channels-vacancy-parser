"""JSON в БД пишется UTF-8 без \\uXXXX-эскейпов."""

from __future__ import annotations

from sqlalchemy import JSON, Column, Integer, MetaData, Table, text

from backend.db.session import get_engine, reset_engine


def test_json_columns_store_cyrillic_without_unicode_escapes(monkeypatch, tmp_path):
    db_file = tmp_path / "json_enc.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    from backend.settings import get_settings

    get_settings.cache_clear()
    reset_engine()

    engine = get_engine()
    md = MetaData()
    t = Table("t", md, Column("id", Integer, primary_key=True), Column("j", JSON))
    md.create_all(engine)

    payload = {"requirement": "Хорошо понимаешь HTTP"}
    with engine.begin() as conn:
        conn.execute(t.insert().values(id=1, j=payload))
        raw = conn.execute(text("SELECT j FROM t")).scalar_one()

    assert "Хорошо" in raw
    assert "\\u" not in raw

    reset_engine()
    get_settings.cache_clear()
