"""Актуальность сохранённых оценки и письма: запрошенная модель и версия промпта."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from tests.conftest import add_legacy_vacancy, new_user

from backend.db.migrate import alembic_ini_path
from backend.db.models import ResumeRow
from backend.services.llm_result_freshness import cover_letter_is_stale, match_is_stale
from jobdesk.ai.prompts import COVER_LETTER_PROMPT_VERSION, MATCH_PROMPT_VERSION
from jobdesk.config.schema import AiConfig


def test_migration_backfills_requested_model(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "029_parse_runs_drop_pending")

    engine = create_engine(pg_empty_db)
    with sessionmaker(bind=engine)() as s:
        user_id = new_user(s)
        resume = ResumeRow(user_id=user_id, name="CV", text="python")
        s.add(resume)
        s.flush()
        add_legacy_vacancy(
            s, id="v1", source_post_key="ch-v1", source_channel="ch", source_url="u", raw_text="x", cleaned_text="x"
        )
        params = {"r": resume.id, "v": "v1"}
        s.execute(
            text(
                "INSERT INTO vacancy_matches (resume_id, vacancy_id, overall_score, confidence, model)"
                " VALUES (:r, :v, 70, 80, 'fallback/b')"
            ),
            params,
        )
        s.execute(
            text(
                "INSERT INTO cover_letters (resume_id, vacancy_id, text, tone, model)"
                " VALUES (:r, :v, 'Письмо', 'neutral', 'model/a')"
            ),
            params,
        )
        s.commit()

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        match = conn.execute(text("SELECT model, requested_model, prompt_version FROM vacancy_matches")).one()
        letter = conn.execute(text("SELECT model, requested_model, prompt_version FROM cover_letters")).one()
    assert tuple(match) == ("fallback/b", "fallback/b", None)
    assert tuple(letter) == ("model/a", "model/a", None)

    command.downgrade(cfg, "029_parse_runs_drop_pending")
    with engine.connect() as conn:
        cols = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name IN ('vacancy_matches', 'cover_letters')"
                " AND column_name IN ('requested_model', 'prompt_version')"
            )
        ).all()
    assert cols == []
    engine.dispose()


def _row(model: str, requested_model: str | None, prompt_version: str | None) -> SimpleNamespace:
    return SimpleNamespace(model=model, requested_model=requested_model, prompt_version=prompt_version)


@pytest.mark.parametrize(
    ("row", "ai", "stale"),
    [
        pytest.param(_row("a", "a", MATCH_PROMPT_VERSION), {"match_model": "a"}, False, id="fresh"),
        pytest.param(_row("a", "a", MATCH_PROMPT_VERSION), {"match_model": "b"}, True, id="model-changed"),
        pytest.param(_row("a", "a", "match-v1"), {"match_model": "a"}, True, id="prompt-version-changed"),
        pytest.param(_row("fb", "a", MATCH_PROMPT_VERSION), {"match_model": "a"}, False, id="fallback-same-config"),
        pytest.param(_row("a", "a", None), {"match_model": "a"}, False, id="no-version-not-stale-by-version"),
        pytest.param(_row("a", None, None), {"match_model": "a"}, False, id="no-requested-uses-model"),
        pytest.param(_row("fb", None, None), {"match_model": "a"}, True, id="legacy-fallback-row"),
        pytest.param(_row("p", "p", MATCH_PROMPT_VERSION), {"model": "p"}, False, id="match-model-falls-back-to-model"),
    ],
)
def test_match_is_stale(row, ai, stale):
    assert match_is_stale(row, AiConfig(**{"model": "p", **ai})) is stale


@pytest.mark.parametrize(
    ("row", "ai", "stale"),
    [
        pytest.param(_row("w", "w", COVER_LETTER_PROMPT_VERSION), {"cover_letter_model": "w"}, False, id="fresh"),
        pytest.param(
            _row("w", "w", COVER_LETTER_PROMPT_VERSION), {"cover_letter_model": "x"}, True, id="model-changed"
        ),
        pytest.param(_row("w", "w", "cover-letter-v0"), {"cover_letter_model": "w"}, True, id="version-changed"),
        pytest.param(_row("fb", "w", COVER_LETTER_PROMPT_VERSION), {"cover_letter_model": "w"}, False, id="fallback"),
        pytest.param(_row("w", None, None), {"cover_letter_model": "w"}, False, id="legacy-row"),
        # пустой cover_letter_model → модель матчинга → основная модель
        pytest.param(_row("m", "m", COVER_LETTER_PROMPT_VERSION), {"match_model": "m"}, False, id="via-match-model"),
        pytest.param(
            _row("m", "m", COVER_LETTER_PROMPT_VERSION), {"match_model": "m2"}, True, id="match-model-changed"
        ),
        pytest.param(_row("p", "p", COVER_LETTER_PROMPT_VERSION), {}, False, id="via-model"),
    ],
)
def test_cover_letter_is_stale(row, ai, stale):
    assert cover_letter_is_stale(row, AiConfig(**{"model": "p", **ai})) is stale
