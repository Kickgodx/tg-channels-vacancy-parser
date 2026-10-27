"""CLI: дамп и импорт PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import typer

from backend.db.session import session_scope
from backend.db.transfer import ImportMode, count_replace_losses, import_dump_file, write_dump_file
from tg_jobs_parser.utils.env_loader import try_load_dotenv

db_app = typer.Typer(help="Дамп и импорт данных PostgreSQL (перенос между инстансами).")


@db_app.command("dump")
def dump_cmd(
    out: Path = typer.Option(
        Path("backup/tgjobs_dump.json"),
        "--out",
        "-o",
        help="Путь к JSON-файлу дампа",
    ),
) -> None:
    """Экспорт всех таблиц приложения (прогоны, вакансии, курсы, кэш LLM)."""
    try_load_dotenv()
    with session_scope() as session:
        stats = write_dump_file(session, out)
    typer.echo(
        typer.style(
            f"Дамп сохранён: {out.resolve()} "
            f"(прогонов={stats.parse_runs}, вакансий={stats.vacancies}, "
            f"курсов={stats.exchange_rates}, кэш LLM={stats.ai_extraction_cache})",
            fg=typer.colors.GREEN,
        )
    )


@db_app.command("import")
def import_cmd(
    path: Path = typer.Option(
        ...,
        "--from",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON-файл дампа",
    ),
    mode: ImportMode = typer.Option(
        "merge",
        "--mode",
        "-m",
        help="merge — UPSERT по PK; replace — очистить таблицы и загрузить заново",
    ),
) -> None:
    """Импорт дампа в текущую БД (DATABASE_URL из .env)."""
    try_load_dotenv()
    if mode not in ("merge", "replace"):
        typer.echo(
            typer.style("mode должен быть merge или replace", fg=typer.colors.RED),
            err=True,
        )
        raise typer.Exit(2)
    if mode == "replace":
        with session_scope() as session:
            losses = count_replace_losses(session)
        typer.confirm(
            "Режим replace удалит все прогоны, вакансии, курсы и кэш LLM в целевой БД, "
            f"а вместе с вакансиями — оценки соответствия ({losses.vacancy_matches}), "
            f"сопроводительные письма ({losses.cover_letters}), адаптации резюме ({losses.resume_tailorings}), "
            f"подготовку к собеседованию ({losses.interview_preps}) и LLM-задачи по вакансиям "
            f"({losses.llm_jobs}) пользователей: в дамп они не входят. Продолжить?",
            abort=True,
        )
    with session_scope() as session:
        stats = import_dump_file(session, path, mode=mode)
    typer.echo(
        typer.style(
            f"Импорт ({mode}) завершён: прогонов={stats.parse_runs}, вакансий={stats.vacancies}, "
            f"курсов={stats.exchange_rates}, кэш LLM={stats.ai_extraction_cache}",
            fg=typer.colors.GREEN,
        )
    )
