"""Typer CLI: parse, analyze, export, run-web."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from tg_jobs_parser.analytics.charts import save_matplotlib_charts
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.config.schema import (
    AiConfig,
    AppConfig,
    FiltersConfig,
    OutputConfig,
    SearchConfig,
    SourcesConfig,
    TelegramConfig,
)
from tg_jobs_parser.exporters.excel_exporter import export_excel
from tg_jobs_parser.utils.sorting import sort_vacancies_for_report
from tg_jobs_parser.exporters.json_exporter import export_json, load_parse_result_json
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_runner import run_parse
from tg_jobs_parser.utils.logger import get_logger, setup_logging

app = typer.Typer(help="Парсер IT-вакансий из Telegram-каналов через Telethon (MVP).")
_LOG = get_logger(__name__)


def _build_config_from_cli(
    base: AppConfig,
    channels: list[str] | None,
    date_from: str | None,
    date_to: str | None,
    specs: list[str] | None,
    exclude: list[str] | None,
    countries: list[str] | None,
    cities: list[str] | None,
    work_formats: list[str] | None,
    output_dir: str | None,
    threshold: int | None,
    telegram_session: str | None,
    ai_enabled: bool | None = None,
) -> AppConfig:
    data = base.model_dump()
    if channels:
        data["sources"] = SourcesConfig(channels=list(dict.fromkeys(channels))).model_dump()
    search_updates = {}
    if date_from:
        search_updates["date_from"] = date_from
    if date_to:
        search_updates["date_to"] = date_to
    if search_updates:
        merged_search = {**data.get("search", {}), **search_updates}
        data["search"] = SearchConfig(**merged_search).model_dump()
    filt_updates = {}
    if specs is not None:
        filt_updates["specializations"] = specs
    if exclude is not None:
        filt_updates["exclude_keywords"] = exclude
    if countries is not None:
        filt_updates["countries"] = countries
    if cities is not None:
        filt_updates["cities"] = cities
    if work_formats is not None:
        filt_updates["work_formats"] = work_formats
    if filt_updates:
        merged_f = {**data.get("filters", {}), **filt_updates}
        data["filters"] = FiltersConfig(**merged_f).model_dump()
    if output_dir:
        out = OutputConfig(**{**data.get("output", {}), "directory": output_dir})
        data["output"] = out.model_dump()
    if threshold is not None:
        p = dict(data.get("parser", {}))
        p["vacancy_score_threshold"] = threshold
        data["parser"] = p

    tg_updates: dict[str, str] = {}
    if telegram_session:
        tg_updates["telethon_session_file"] = telegram_session
    if tg_updates:
        merged_tg = {**data.get("telegram", {}), **tg_updates}
        data["telegram"] = TelegramConfig(**merged_tg).model_dump()

    if ai_enabled is not None:
        merged_ai = {**data.get("ai", {}), "enabled": ai_enabled}
        data["ai"] = AiConfig(**merged_ai).model_dump()

    return AppConfig.model_validate(data)


@app.command()
def parse(
    config_path: Optional[Path] = typer.Option(
        Path("config.yaml"),
        "--config",
        "-c",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Путь к config.yaml",
    ),
    channels: Optional[str] = typer.Option(None, "--channels", help="Список каналов через запятую"),
    date_from: Optional[str] = typer.Option(None, "--date-from"),
    date_to: Optional[str] = typer.Option(None, "--date-to"),
    specializations: Optional[str] = typer.Option(None, "--specializations", help="Через запятую"),
    exclude_keywords: Optional[str] = typer.Option(None, "--exclude-keywords"),
    countries: Optional[str] = typer.Option(None, "--countries"),
    cities: Optional[str] = typer.Option(None, "--cities"),
    work_formats: Optional[str] = typer.Option(
        None,
        "--work-formats",
        help="Через запятую: remote,hybrid,office,relocation,any,exclude_office",
    ),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o"),
    threshold: Optional[int] = typer.Option(None, "--threshold"),
    telegram_session: Optional[str] = typer.Option(
        None,
        "--telegram-session",
        help="Путь к файлу сессии Telethon (переопределение конфига).",
    ),
    skip_charts: bool = typer.Option(False, "--skip-charts"),
    ai: bool = typer.Option(False, "--ai", help="Включить LLM-извлечение (OpenRouter, см. config ai.*)"),
) -> None:
    """Запуск парсинга и экспорта Excel/JSON."""
    setup_logging()
    cfg = load_config(config_path)

    def split_csv(s: str | None) -> list[str] | None:
        if s is None:
            return None
        return [p.strip() for p in s.split(",") if p.strip()]

    ch_list = split_csv(channels)
    specs = split_csv(specializations)
    excl = split_csv(exclude_keywords)
    ctr = split_csv(countries)
    cts = split_csv(cities)
    wf = split_csv(work_formats)

    cfg = _build_config_from_cli(
        cfg,
        channels=ch_list,
        date_from=date_from,
        date_to=date_to,
        specs=specs,
        exclude=excl,
        countries=ctr,
        cities=cts,
        work_formats=wf,
        output_dir=output_dir,
        threshold=threshold,
        telegram_session=telegram_session,
        ai_enabled=ai if ai else None,
    )

    if ai and not cfg.ai.enabled:
        cfg = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": True})})

    out_dir = Path(cfg.output.directory)
    out_dir.mkdir(parents=True, exist_ok=True)

    overrides = {
        "channels": ch_list,
        "date_from": date_from,
        "date_to": date_to,
        "config_path": str(config_path),
        "ai": ai,
    }
    result = run_parse(cfg, cli_overrides=overrides)

    xlsx_path = out_dir / cfg.output.excel_filename
    json_path = out_dir / cfg.output.json_filename
    charts_dir = out_dir / cfg.output.charts_directory

    export_excel(
        xlsx_path,
        result.vacancies_all,
        result.vacancies_unique,
        result.duplicates,
        result.metrics,
    )
    export_json(result, json_path)
    _LOG.info("Excel: %s", xlsx_path.resolve())
    _LOG.info("JSON: %s", json_path.resolve())

    if not skip_charts:
        saved = save_matplotlib_charts(result.metrics, charts_dir)
        for p in saved:
            _LOG.info("График: %s", p.resolve())

    typer.echo(
        typer.style(
            f"Готово. Уникальных вакансий: {result.metrics.unique_vacancies}; "
            f"дубликатов: {result.metrics.duplicate_count}",
            fg=typer.colors.GREEN,
        )
    )
    ai_stats = (result.run_params or {}).get("ai_stats")
    if ai_stats:
        tin = int(ai_stats.get("tokens_in") or 0)
        tout = int(ai_stats.get("tokens_out") or 0)
        typer.echo(
            typer.style(
                f"AI токены за прогон: input={tin}, output={tout}, всего={tin + tout}",
                fg=typer.colors.CYAN,
            )
        )
        typer.echo(
            typer.style(
                f"AI: вызовов {ai_stats.get('calls', 0)}, кэш {ai_stats.get('cache_hits', 0)}, "
                f"без LLM (низкий score) {ai_stats.get('skipped_score', 0)}, "
                f"не-вакансий {ai_stats.get('dropped_not_job', 0)}, "
                f"rate-limit {ai_stats.get('rate_limit_skipped', 0)}.",
                fg=typer.colors.CYAN,
            )
        )


@app.command("analyze")
def analyze_cmd(
    json_path: Path = typer.Option(..., "--from-json", exists=True, dir_okay=False, readable=True),
) -> None:
    """Краткая сводка по сохранённому JSON."""
    data = load_parse_result_json(json_path)
    m = data.get("metrics", {})
    slim = {
        "total_posts_fetched": m.get("total_posts_fetched"),
        "total_vacancies": m.get("total_vacancies"),
        "unique_vacancies": m.get("unique_vacancies"),
        "duplicate_count": m.get("duplicate_count"),
        "vacancies_with_salary": m.get("vacancies_with_salary"),
        "vacancies_without_salary": m.get("vacancies_without_salary"),
        "vacancies_by_channel": m.get("vacancies_by_channel"),
        "vacancies_by_specialization": m.get("vacancies_by_specialization"),
        "warnings": m.get("warnings"),
    }
    typer.echo(json.dumps(slim, ensure_ascii=False, indent=2))


@app.command("export")
def export_cmd(
    json_path: Path = typer.Option(..., "--from-json", exists=True, dir_okay=False, readable=True),
    out: Path = typer.Option(Path("reexport.xlsx"), "--out"),
) -> None:
    """Пересобрать Excel из JSON (без повторного парсинга)."""
    data = load_parse_result_json(json_path)
    vac_all = [Vacancy.model_validate(v) for v in data.get("vacancies_all", [])]
    vac_u = [Vacancy.model_validate(v) for v in data.get("vacancies_unique", [])]
    vac_d = [Vacancy.model_validate(v) for v in data.get("duplicates", [])]
    from tg_jobs_parser.models.metrics import MetricsSummary

    metrics = MetricsSummary.model_validate(data.get("metrics", {}))
    sort_vacancies_for_report(vac_all)
    sort_vacancies_for_report(vac_u)
    sort_vacancies_for_report(vac_d)
    export_excel(out, vac_all, vac_u, vac_d, metrics)
    typer.echo(typer.style(f"Excel сохранён: {out.resolve()}", fg=typer.colors.GREEN))


def _terminate_streamlit(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


@app.command("run-web")
def run_web(
    port: int = typer.Option(8501, "--port"),
) -> None:
    """Запуск Streamlit UI."""
    here = Path(__file__).resolve().parent.parent / "web" / "streamlit_app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(here), "--server.port", str(port)]
    if sys.platform == "win32":
        proc = subprocess.Popen(cmd)
        try:
            raise SystemExit(proc.wait())
        except KeyboardInterrupt:
            typer.echo("Остановка Streamlit…", err=True)
            _terminate_streamlit(proc)
            raise SystemExit(0) from None
    raise SystemExit(subprocess.call(cmd))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
