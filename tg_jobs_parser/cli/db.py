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


@db_app.command("merge-duplicates")
def merge_duplicates_cmd(
    apply: bool = typer.Option(False, "--apply", help="Склеить; без флага — только показать пары"),
    limit: int = typer.Option(20, "--show", help="Сколько пар показать"),
) -> None:
    """Склеить почти-дубли Telegram, накопленные до дедупа между прогонами (разово)."""
    try_load_dotenv()
    from backend.services.near_duplicate_merge import find_near_duplicates, merge_all
    from backend.settings import get_settings
    from tg_jobs_parser.config.loader import load_config

    config = load_config(get_settings().config_path)
    with session_scope() as session:
        pairs = find_near_duplicates(session, config)
        for p in pairs[:limit]:
            typer.echo(f"{p.duplicate_id[:12]} → {p.canonical_id[:12]}  ({p.reason})")
        if not apply:
            typer.echo(f"Найдено пар: {len(pairs)} (первый проход; склейка может найти и цепочки). Склеить: --apply")
            return
        merged, passes = merge_all(session, config)
    typer.echo(typer.style(f"Склеено копий: {merged} за {passes} проход(а)", fg=typer.colors.GREEN))


@db_app.command("clean-vacancies")
def clean_vacancies_cmd(
    apply: bool = typer.Option(False, "--apply", help="Применить; без флага — только показать план"),
    limit: int = typer.Option(40, "--show", help="Сколько удаляемых постов и технологий показать"),
) -> None:
    """Удалить посты соискателей и подборки вакансий, почистить технологии (разово)."""
    try_load_dotenv()
    from backend.services.vacancy_cleanup import apply_cleanup, plan_cleanup

    with session_scope() as session:
        plan = plan_cleanup(session)
        typer.echo(f"Не вакансии (соискатели, подборки) — удалить: {len(plan.delete_ids)}")
        for preview in plan.delete_previews[:limit]:
            typer.echo(f"  - {preview}")
        typer.echo(
            f"Технологии — переписать у {len(plan.technology_updates)} вакансий: "
            f"убрать {sum(plan.removed.values())}, добавить {sum(plan.added.values())} упоминаний"
        )
        typer.echo("  убираются: " + ", ".join(f"{t} ({n})" for t, n in plan.removed.most_common(limit)))
        typer.echo("  добавляются: " + ", ".join(f"{t} ({n})" for t, n in plan.added.most_common(limit)))
        if not apply:
            typer.echo("Применить: --apply")
            return
        deleted, updated = apply_cleanup(session, plan)
    typer.echo(typer.style(f"Удалено: {deleted}, технологии переписаны: {updated}", fg=typer.colors.GREEN))
