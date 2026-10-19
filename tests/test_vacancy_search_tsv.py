"""Миграция 033: хранимый ``vacancies.search_tsv`` и его индекс."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.db.migrate import alembic_ini_path


def _indexes(conn) -> set[str]:
    return set(conn.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename = 'vacancies'")))


def test_search_tsv_migration_fills_existing_rows_and_downgrades(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "032_llm_jobs_quota_day")

    engine = create_engine(pg_empty_db)
    with engine.begin() as conn:
        # Колонки — на ревизии 032; модель уже знает search_tsv, поэтому сырой SQL.
        conn.execute(
            text(
                "INSERT INTO vacancies (id, source_post_key, source_channel, source_url, raw_text, cleaned_text,"
                " detected_specializations, detected_grade, technologies, application_urls, extraction_source,"
                " vacancy_score, vacancy_substantive_score, is_duplicate, parsed_at, updated_at)"
                " VALUES ('v1', 'c-1', 'c', 'https://t.me/s/c', 'x', 'Ищем разработчиков Python',"
                " '[]', 'unknown', '[]', '[]', 'rules', 0, 0, false, now(), now())"
            )
        )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        found = conn.scalar(
            text("SELECT count(*) FROM vacancies WHERE search_tsv @@ websearch_to_tsquery('russian', 'разработчик')")
        )
        assert found == 1
        assert "ix_vacancies_search_tsv" in _indexes(conn)
        assert "ix_vacancies_fts" not in _indexes(conn)
        conn.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            conn.scalars(
                text("EXPLAIN SELECT id FROM vacancies WHERE search_tsv @@ websearch_to_tsquery('russian', 'python')")
            )
        )
        assert "ix_vacancies_search_tsv" in plan

    command.downgrade(cfg, "032_llm_jobs_quota_day")
    with engine.connect() as conn:
        columns = set(
            conn.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'vacancies'"))
        )
        assert "search_tsv" not in columns
        assert "ix_vacancies_fts" in _indexes(conn)
    engine.dispose()
