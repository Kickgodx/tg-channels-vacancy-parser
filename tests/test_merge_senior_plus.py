"""Грейд ``senior+`` слит с ``senior``: правила, нормализация, миграция 035."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.db.migrate import alembic_ini_path
from tg_jobs_parser.extraction.grade_extractor import GRADE_OPTIONS, extract_grade, normalize_grade


def test_senior_plus_is_not_a_grade():
    assert "senior+" not in GRADE_OPTIONS
    assert extract_grade("Senior+ Python Developer") == "senior"
    assert extract_grade("Ищем сеньор+ на Go") == "senior"
    assert extract_grade("Senior+ / Lead") == "lead"


def test_legacy_senior_plus_normalizes_to_senior():
    # Ответ LLM и записи кэша extraction до extract-v8.
    assert normalize_grade("senior+") == "senior"
    assert normalize_grade("Senior +") == "senior"


def test_migration_merges_senior_plus(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "034_vacancies_sort_indexes")

    engine = create_engine(pg_empty_db)
    with engine.begin() as conn:
        for vid, grade in (("v1", "senior+"), ("v2", "middle+"), ("v3", "senior")):
            conn.execute(
                text(
                    "INSERT INTO vacancies (id, source_post_key, source_channel, source_url, raw_text,"
                    " cleaned_text, detected_specializations, detected_grade, technologies, application_urls,"
                    " extraction_source, vacancy_score, vacancy_substantive_score, is_duplicate, parsed_at,"
                    " updated_at) VALUES (:id, :id, 'hh', 'https://hh.ru', 'x', 'x', '[]', :g, '[]', '[]',"
                    " 'hh_api', 0, 0, false, now(), now())"
                ),
                {"id": vid, "g": grade},
            )
        conn.execute(
            text(
                "INSERT INTO saved_filters (name, params) VALUES"
                " ('a', '{\"grade\": \"senior+\", \"page\": 1}'), ('b', '{\"grade\": null}')"
            )
        )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        grades = dict(conn.execute(text("SELECT id, detected_grade FROM vacancies")).all())
        assert grades == {"v1": "senior", "v2": "middle+", "v3": "senior"}
        params = dict(conn.execute(text("SELECT name, params FROM saved_filters")).all())
        assert params == {"a": {"grade": "senior", "page": 1}, "b": {"grade": None}}
    engine.dispose()
