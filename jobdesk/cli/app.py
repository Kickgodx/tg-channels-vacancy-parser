"""Typer CLI: авторизация Telethon и перенос БД."""

from __future__ import annotations

from pathlib import Path

import typer

from jobdesk.cli.db import db_app
from jobdesk.config.loader import load_config
from jobdesk.parsers.telegram_telethon import ensure_telethon_session
from jobdesk.utils.logger import setup_logging

app = typer.Typer(
    help="Авторизация Telethon (файл сессии) и перенос данных PostgreSQL.",
)
app.add_typer(db_app, name="db")


@app.command("login")
def login_cmd(
    config_path: Path | None = typer.Option(
        Path("config.yaml"),
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Путь к config.yaml (берём telegram.telethon_session_file).",
    ),
    telegram_session: str | None = typer.Option(
        None,
        "--telegram-session",
        help="Путь к файлу сессии Telethon (переопределение конфига).",
    ),
) -> None:
    """Интерактивный вход в Telegram; сохраняет файл сессии (sessions/)."""
    setup_logging()
    cfg = load_config(config_path)
    session_file = telegram_session or cfg.telegram.telethon_session_file
    try:
        info = ensure_telethon_session(session_file)
    except KeyboardInterrupt:
        typer.echo(typer.style("Прервано (Ctrl+C).", fg=typer.colors.YELLOW), err=True)
        raise typer.Exit(130) from None
    except RuntimeError as e:
        typer.echo(typer.style(str(e), fg=typer.colors.RED), err=True)
        raise typer.Exit(1) from e

    who = info.username or (str(info.user_id) if info.user_id is not None else "?")
    typer.echo(
        typer.style(
            f"Сессия готова: {info.session_path} (user={who})",
            fg=typer.colors.GREEN,
        )
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
