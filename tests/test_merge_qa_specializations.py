"""Синонимы QA-ролей слиты с каноническими: алиасы, нормализация, миграция 036."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.db.migrate import alembic_ini_path
from jobdesk.ai.schema import VacancyAIExtraction
from jobdesk.config.schema import AppConfig, FiltersConfig, canonical_specializations


def test_canonical_specializations_dedupes_in_order():
    assert canonical_specializations(["Тестировщик", "QA", "Manual QA", "AQA", "Python"]) == [
        "QA",
        "Automation QA",
        "Python",
    ]
    assert canonical_specializations(["Программист, разработчик"]) == ["Software Engineer"]


def test_llm_answer_and_cache_normalize_old_names():
    # Ответ модели и записи кэша extraction до extract-v9.
    ai = VacancyAIExtraction.model_validate({"detected_specializations": ["Manual QA", "AQA"]})
    assert ai.detected_specializations == ["QA", "Automation QA"]


def test_config_filters_and_dictionary_use_canonical_names():
    assert FiltersConfig(specializations=["AQA", "Тестировщик", "QA"]).specializations == ["Automation QA", "QA"]
    cfg = AppConfig.model_validate({"dictionaries": {"specializations": {"AQA": ["автоматизация тестов"]}}})
    merged = cfg.merged_specializations()
    assert "AQA" not in merged
    assert "автоматизация тестов" in merged["Automation QA"]


def test_migration_merges_qa_specializations(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "035_merge_senior_plus")

    rows = {
        "v1": '["Тестировщик", "QA"]',
        "v2": '["AQA", "Python", "Manual QA"]',
        "v3": '["Программист, разработчик", "Software Engineer", "Go"]',
        "v4": '["Go"]',
        "v5": "[]",
    }
    engine = create_engine(pg_empty_db)
    with engine.begin() as conn:
        for vid, specs in rows.items():
            conn.execute(
                text(
                    "INSERT INTO vacancies (id, source_post_key, source_channel, source_url, raw_text,"
                    " cleaned_text, detected_specializations, detected_grade, technologies, application_urls,"
                    " extraction_source, vacancy_score, vacancy_substantive_score, is_duplicate, parsed_at,"
                    " updated_at) VALUES (:id, :id, 'hh', 'https://hh.ru', 'x', 'x', CAST(:s AS jsonb), 'unknown',"
                    " '[]', '[]', 'hh_api', 0, 0, false, now(), now())"
                ),
                {"id": vid, "s": specs},
            )
        conn.execute(
            text(
                "INSERT INTO saved_filters (name, params) VALUES"
                ' (\'a\', \'{"specializations": ["AQA", "Manual QA", "QA"], "page": 1}\'),'
                " ('b', '{\"specializations\": [\"Go\"]}'), ('c', '{\"grade\": null}')"
            )
        )

    command.upgrade(cfg, "036_merge_qa_specializations")
    with engine.connect() as conn:
        specs = dict(conn.execute(text("SELECT id, detected_specializations FROM vacancies")).all())
        assert specs == {
            "v1": ["QA"],
            "v2": ["Automation QA", "Python", "QA"],
            "v3": ["Software Engineer", "Go"],
            "v4": ["Go"],
            "v5": [],
        }
        params = dict(conn.execute(text("SELECT name, params FROM saved_filters")).all())
        assert params == {
            "a": {"specializations": ["Automation QA", "QA"], "page": 1},
            "b": {"specializations": ["Go"]},
            "c": {"grade": None},
        }
    engine.dispose()
