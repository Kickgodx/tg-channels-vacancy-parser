"""Чекпоинты каналов: воркер передаёт их в run_parse и сохраняет только при completed."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from backend.db import parse_run_repository as pr
from backend.db.channel_checkpoint_repository import load_channel_checkpoints, save_channel_checkpoints
from backend.db.session import init_db, reset_engine, session_scope
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
def _sqlite_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'cp.db').as_posix()}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
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


def test_failed_run_does_not_move_checkpoints(monkeypatch, run_id):
    with session_scope() as session:
        save_channel_checkpoints(session, {"chan": CP_OLD})
    result = ParseResult(channel_checkpoints={"chan": CP_NEW}, incomplete_reason="LLM отключён")
    monkeypatch.setattr(parse_tasks, "run_parse", lambda config, **kwargs: result)

    run_parse_job(str(run_id))

    with session_scope() as session:
        assert pr.get_parse_run(session, run_id).status == "failed"
    assert _stored() == {"chan": CP_OLD}


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
