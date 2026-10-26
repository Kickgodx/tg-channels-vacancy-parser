"""Грейд ``middle+`` слит с ``middle``: правила, нормализация, промпт, миграция 039."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.db.migrate import alembic_ini_path
from tg_jobs_parser.ai.prompts import SYSTEM_PROMPT_V1
from tg_jobs_parser.extraction.grade_extractor import GRADE_OPTIONS, extract_grade, normalize_grade


def test_middle_plus_is_not_a_grade():
    assert "middle+" not in GRADE_OPTIONS
    assert extract_grade("Middle+ Android developer") == "middle"
    assert extract_grade("Ищем мидл+ на Go") == "middle"
    assert extract_grade("Middle+ / Senior") == "senior"


def test_legacy_middle_plus_normalizes_to_middle():
    # Ответ LLM и записи кэша extraction до extract-v11.
    assert normalize_grade("middle+") == "middle"
    assert normalize_grade("Middle +") == "middle"


def test_prompt_does_not_offer_middle_plus():
    prompt = SYSTEM_PROMPT_V1
    assert "middle+," not in prompt
    assert '"Middle+" → middle' in prompt


def test_migration_merges_middle_plus(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "038_collapse_1c_technologies")

    engine = create_engine(pg_empty_db)
    with engine.begin() as conn:
        for vid, grade in (("v1", "middle+"), ("v2", "junior+"), ("v3", "middle")):
            conn.execute(
                text(
                    "INSERT INTO vacancies (id, source_post_key, source_channel, source_url, raw_text,"
                    " cleaned_text, detected_specializations, detected_grade, technologies, application_urls,"
                    " extraction_source, vacancy_score, vacancy_substantive_score, is_duplicate, parsed_at,"
                    " updated_at) VALUES (:id, :id, 'ch', 'https://t.me/s/ch', 'x', 'x', '[]', :g, '[]', '[]',"
                    " 'ai', 0, 0, false, now(), now())"
                ),
                {"id": vid, "g": grade},
            )
        conn.execute(
            text(
                "INSERT INTO saved_filters (name, params) VALUES"
                ' (\'a\', \'{"grade": "middle+", "page": 1}\'), (\'b\', \'{"grade": "junior+"}\')'
            )
        )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        grades = dict(conn.execute(text("SELECT id, detected_grade FROM vacancies")).all())
        assert grades == {"v1": "middle", "v2": "junior+", "v3": "middle"}
        params = dict(conn.execute(text("SELECT name, params FROM saved_filters")).all())
        assert params == {"a": {"grade": "middle", "page": 1}, "b": {"grade": "junior+"}}
    engine.dispose()
