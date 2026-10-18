"""Чекпоинты каналов: воркер передаёт их в run_parse и сохраняет только при completed."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from backend.db import parse_run_repository as pr
from backend.db.channel_checkpoint_repository import (
    format_checkpoint_changes,
    load_channel_checkpoints,
    save_channel_checkpoints,
)
from backend.db.session import reset_engine, session_scope
from backend.settings import get_settings
from backend.workers import parse_tasks
from backend.workers.parse_tasks import run_parse_job
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import AiConfig, AppConfig
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.parse_runner import _checkpoints_if_fully_processed
from tg_jobs_parser.parsers.base import ChannelCheckpoint

CP_OLD = ChannelCheckpoint(last_message_id=100, covered_from=date(2026, 10, 1))
CP_NEW = ChannelCheckpoint(last_message_id=150, covered_from=date(2026, 10, 1))


@pytest.fixture(autouse=True)
def _db(monkeypatch, pg_db):
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def run_id(default_test_config) -> uuid.UUID:
    cfg = load_config(default_test_config)
    cfg = cfg.model_copy(update={"sources": cfg.sources.model_copy(update={"channels": ["@chan", "other"]})})
    with session_scope() as session:
        return pr.create_parse_run(session, config_snapshot=cfg.model_dump(mode="json")).id


def _stored() -> dict[str, ChannelCheckpoint]:
    with session_scope() as session:
        return load_channel_checkpoints(session, ["chan", "other"])


def test_repository_upserts():
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_OLD})
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_NEW, "other": CP_OLD})
    assert _stored() == {"chan": CP_NEW, "other": CP_OLD}


def test_repository_returns_changes():
    with session_scope() as session:
        assert save_channel_checkpoints(session, {"chan": CP_OLD}) == {"chan": (None, CP_OLD)}
    moved = ChannelCheckpoint(last_message_id=150, covered_from=date(2026, 10, 5))
    with session_scope() as session:
        changes = save_channel_checkpoints(session, {"chan": moved, "other": CP_OLD})
    assert changes == {"chan": (CP_OLD, moved), "other": (None, CP_OLD)}
    assert format_checkpoint_changes(changes) == (
        "@chan 100→150 (с 2026-10-01→2026-10-05), @other новый 100 (с 2026-10-01)"
    )
    with session_scope() as session:
        assert save_channel_checkpoints(session, {"chan": moved}) == {}  # те же значения — не изменение
    assert format_checkpoint_changes({"chan": (CP_OLD, CP_NEW)}) == "@chan 100→150 (с 2026-10-01)"


def test_completed_run_passes_and_saves_checkpoints(monkeypatch, run_id):
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_OLD})
    seen = {}

    def fake_run_parse(config, **kwargs):
        seen.update(kwargs["channel_checkpoints"])
        return ParseResult(channel_checkpoints={"chan": CP_NEW})

    monkeypatch.setattr(parse_tasks, "run_parse", fake_run_parse)
    run_parse_job(str(run_id))

    assert seen == {"chan": CP_OLD}  # имя канала нормализовано: "@chan" → "chan"
    assert _stored() == {"chan": CP_NEW}


def test_partial_run_does_not_move_checkpoints(monkeypatch, run_id):
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_OLD})
    result = ParseResult(channel_checkpoints={"chan": CP_NEW}, incomplete_reason="LLM отключён")
    monkeypatch.setattr(parse_tasks, "run_parse", lambda config, **kwargs: result)

    run_parse_job(str(run_id))

    with session_scope() as session:
        assert pr.get_parse_run(session, run_id).status == "partial"
    assert _stored() == {"chan": CP_OLD}


def test_checkpoint_saved_mid_run_survives_partial_run(monkeypatch, run_id):
    """Канал, разобранный и записанный до потери LLM, не перечитывается следующим прогоном."""
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_OLD, "other": CP_OLD})

    def fake_run_parse(config, **kwargs):
        kwargs["checkpoint_sink"]({"chan": CP_NEW})
        return ParseResult(channel_checkpoints={}, incomplete_reason="LLM отключён")

    monkeypatch.setattr(parse_tasks, "run_parse", fake_run_parse)
    run_parse_job(str(run_id))

    with session_scope() as session:
        assert pr.get_parse_run(session, run_id).status == "partial"
    assert _stored() == {"chan": CP_NEW, "other": CP_OLD}


def test_incremental_fetch_off_passes_no_checkpoints(monkeypatch, default_test_config):
    cfg = load_config(default_test_config)
    cfg = cfg.model_copy(update={"parser": cfg.parser.model_copy(update={"incremental_fetch": False})})
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot=cfg.model_dump(mode="json")).id
        save_channel_checkpoints(session, {"chan": CP_OLD})
    seen = []
    monkeypatch.setattr(
        parse_tasks, "run_parse", lambda config, **kw: seen.append(kw["channel_checkpoints"]) or ParseResult()
    )
    run_parse_job(str(rid))
    assert seen == [None]


def _ai_service(errors: int):
    return SimpleNamespace(stats=SimpleNamespace(errors=errors))


@pytest.mark.parametrize(
    ("mode", "errors", "incomplete", "expected"),
    [
        ("primary", 0, None, {"chan": CP_NEW}),
        ("primary", 2, None, {}),  # посты, отброшенные LLM, следующий прогон должен перечитать
        ("assist", 2, None, {"chan": CP_NEW}),  # в assist такие посты разобраны rules
        ("primary", 0, "LLM отключён", {}),
    ],
)
def test_checkpoints_only_when_no_post_lost(mode, errors, incomplete, expected):
    fetcher = SimpleNamespace(new_checkpoints={"chan": CP_NEW})
    warnings: list[str] = []
    out = _checkpoints_if_fully_processed(
        fetcher,
        config=AppConfig(ai=AiConfig(mode=mode)),
        ai_service=_ai_service(errors),
        incomplete_reason=incomplete,
        all_warnings=warnings,
    )
    assert out == expected
    assert bool(warnings) == (mode == "primary" and errors > 0 and not incomplete)


def test_max_posts_per_run_in_primary_stops_checkpoints(monkeypatch, default_test_config):
    """primary, потолок 2: канал, разобранный до потолка, сдвинут; канал с пропущенными постами — нет."""
    from tests.test_streaming_parse import _post, _StreamingParser

    import tg_jobs_parser.parse_runner as parse_runner
    from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient
    from tg_jobs_parser.ai.schema import VacancyAIExtraction

    cfg = load_config(default_test_config)
    cfg = cfg.model_copy(
        update={
            "sources": cfg.sources.model_copy(update={"channels": ["chan", "other"]}),
            "ai": cfg.ai.model_copy(
                update={"enabled": True, "mode": "primary", "max_posts_per_run": 2, "concurrency": 1}
            ),
            "parser": cfg.parser.model_copy(update={"vacancy_fuzzy_duplicate_threshold": 101}),
        }
    )
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_OLD, "other": CP_OLD})
        rid = pr.create_parse_run(session, config_snapshot=cfg.model_dump(mode="json")).id

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        OpenRouterClient,
        "extract_with_fallback",
        lambda self, *, text, search_context: (VacancyAIExtraction(is_job_post=True, confidence=0.9), LLMUsage()),
    )
    parser = _StreamingParser(
        {"chan": [_post("chan", i) for i in range(2)], "other": [_post("other", i) for i in range(3)]}
    )
    parser.new_checkpoints = {}
    monkeypatch.setattr(parse_runner, "_channel_parser", lambda config, cancellation=None: parser)

    run_parse_job(str(rid))

    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row.status == "partial", row.error_message
        assert "ai.max_posts_per_run=2" in row.error_message
    stored = _stored()
    assert stored["chan"] != CP_OLD  # разобран целиком до потолка — сдвинут по ходу прогона
    assert stored["other"] == CP_OLD  # посты сверх потолка потеряны — следующий прогон перечитает
