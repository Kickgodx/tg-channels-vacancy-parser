"""CLI: login + db; parse/analyze/export удалены."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from jobdesk.cli.app import app
from jobdesk.parsers.telegram_telethon import (
    TelethonSessionInfo,
    ensure_telethon_session,
)


def test_cli_commands_surface():
    names = {c.name for c in app.registered_commands}
    group_names = {g.name for g in app.registered_groups}
    assert "login" in names
    assert "db" in group_names
    assert not {"parse", "analyze", "export"} & names


def test_login_help():
    r = CliRunner().invoke(app, ["login", "--help"])
    assert r.exit_code == 0
    assert "--config" in r.stdout
    assert "--telegram-session" in r.stdout


def test_ensure_telethon_session_writes_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    session = tmp_path / "sessions" / "tg.session"
    me = SimpleNamespace(id=42, username="tester")

    client = MagicMock()
    client.start = AsyncMock()
    client.get_me = AsyncMock(return_value=me)
    client.is_connected = MagicMock(return_value=True)
    client.disconnect = AsyncMock()

    monkeypatch.setattr(
        "jobdesk.parsers.telegram_telethon.telegram_api_credentials",
        lambda: (1, "hash"),
    )
    monkeypatch.setattr(
        "jobdesk.parsers.telegram_telethon.telethon_import_error",
        lambda: None,
    )

    class _FakeTelegramClient:
        def __init__(self, *args, **kwargs):
            pass

        def __new__(cls, *args, **kwargs):
            return client

    import telethon

    monkeypatch.setattr(telethon, "TelegramClient", _FakeTelegramClient)

    info = ensure_telethon_session(session)
    assert isinstance(info, TelethonSessionInfo)
    assert info.session_path == session.resolve()
    assert info.user_id == 42
    assert info.username == "tester"
    client.start.assert_awaited_once()
    client.disconnect.assert_awaited_once()


def _dump_with_user_data(tmp_path: Path) -> Path:
    from tests.test_db_transfer import seed_user_data

    from backend.db.session import session_scope
    from backend.db.transfer import write_dump_file

    dump = tmp_path / "dump.json"
    with session_scope() as session:
        seed_user_data(session)
        write_dump_file(session, dump)
    return dump


def _user_data_counts() -> tuple[int, int, int]:
    from backend.db.session import session_scope
    from backend.db.transfer import count_replace_losses

    with session_scope() as session:
        losses = count_replace_losses(session)
    return losses.vacancy_matches, losses.cover_letters, losses.llm_jobs


def test_db_import_replace_names_user_data_and_aborts_on_no(pg_db, tmp_path: Path):
    dump = _dump_with_user_data(tmp_path)

    r = CliRunner().invoke(app, ["db", "import", "-f", str(dump), "--mode", "replace"], input="n\n")

    assert r.exit_code == 1  # typer.Abort
    assert "оценки соответствия (2)" in r.output
    assert "сопроводительные письма (1)" in r.output
    assert "LLM-задачи по вакансиям (3)" in r.output
    assert "Импорт (replace) завершён" not in r.output
    assert _user_data_counts() == (2, 1, 3)


def test_db_import_merge_does_not_ask(pg_db, tmp_path: Path):
    dump = _dump_with_user_data(tmp_path)

    r = CliRunner().invoke(app, ["db", "import", "-f", str(dump)])

    assert r.exit_code == 0, r.output
    assert "Продолжить?" not in r.output
    assert "Импорт (merge) завершён" in r.output
    assert _user_data_counts() == (2, 1, 3)
