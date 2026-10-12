"""CLI: login + db; parse/analyze/export удалены."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from tg_jobs_parser.cli.app import app
from tg_jobs_parser.parsers.telegram_telethon import (
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
        "tg_jobs_parser.parsers.telegram_telethon.telegram_api_credentials",
        lambda: (1, "hash"),
    )
    monkeypatch.setattr(
        "tg_jobs_parser.parsers.telegram_telethon.telethon_import_error",
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
