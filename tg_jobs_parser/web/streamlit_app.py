"""Streamlit UI для локального MVP."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from tg_jobs_parser.analytics.charts import plotly_daily_line, plotly_grade_bar, plotly_spec_bar
from tg_jobs_parser.channel_search import search_channels
from tg_jobs_parser.config.loader import ConfigLoadError, load_config
from tg_jobs_parser.config.schema import DEFAULT_SPECIALIZATIONS
from tg_jobs_parser.exporters.excel_exporter import export_excel
from tg_jobs_parser.exporters.json_exporter import export_json
from tg_jobs_parser.parse_runner import run_parse
from tg_jobs_parser.utils.logger import setup_logging

st.set_page_config(page_title="Telegram Jobs MVP", layout="wide")
setup_logging()


def default_config_path() -> Path:
    cand = Path("config.yaml")
    if cand.is_file():
        return cand
    return Path(__file__).resolve().parents[2] / "config.yaml"


st.title("Парсер IT-вакансий из публичных Telegram-каналов (MVP)")

with st.sidebar:
    st.header("Источники")
    cfg_path = st.text_input("Путь к config.yaml", value=str(default_config_path()))
    channel_text = st.text_area(
        "Каналы (по строке или через запятую)",
        placeholder="python_django_jobs\nhttps://t.me/devops_jobs",
        height=120,
    )

    kw = st.text_input("Поиск каналов (best-effort, DuckDuckGo)", value="python jobs telegram channel")
    if st.button("Найти кандидатов каналов"):
        found, backend = search_channels(kw)
        st.caption(f"Бэкенд: {backend}")
        st.write(found or "(ничего не найдено — провайдер недоступен или верстка изменилась)")

    tg_mode_preview = "web"
    try:
        if Path(cfg_path).is_file():
            tg_mode_preview = load_config(cfg_path).telegram.mode
    except ConfigLoadError:
        pass

    telegram_mode_sel = st.selectbox(
        "Режим чтения Telegram",
        options=["web", "telethon"],
        index=0 if tg_mode_preview == "web" else 1,
        help="telethon нужен для глубины истории; первая авторизация надёжнее из консоли (CLI).",
    )
    telethon_session_path = st.text_input(
        "Файл сессии Telethon",
        value="sessions/tg_jobs.session",
    )
    if telegram_mode_sel == "telethon":
        st.caption(
            "Нужны TELEGRAM_API_ID / TELEGRAM_API_HASH в .env и установка pip install '.[telethon]'. "
            "Перед Streamlit авторизуйтесь хотя бы раз через CLI, чтобы создать файл сессии."
        )

    st.header("Фильтры")
    all_specs = list(DEFAULT_SPECIALIZATIONS.keys())
    chosen_specs = st.multiselect("Специализации", options=all_specs, default=["Python", "Backend", "QA"])

    exclusions_text = ""
    try:
        if Path(cfg_path).is_file():
            cfg0 = load_config(cfg_path)
            exclusions_text = "\n".join(cfg0.filters.exclude_keywords)
    except ConfigLoadError:
        pass

    exclusions_input = st.text_area(
        "Исключающие ключевые слова (строчки)",
        value=exclusions_text,
        height=120,
    )

    date_from = st.date_input("date_from")
    date_to = st.date_input("date_to")
    countries = st.text_input("Страны (через запятую, пусто = все)", "")
    cities = st.text_input("Города (через запятую)", "")
    work_opts = ["any", "remote", "hybrid", "office", "relocation", "exclude_office"]
    wf = st.multiselect(
        "Формат работы",
        options=work_opts,
        default=["any"],
    )

    parser_sidebar: object | None = None
    try:
        if Path(cfg_path).is_file():
            parser_sidebar = load_config(cfg_path).parser
    except ConfigLoadError:
        parser_sidebar = None
    _thr_sl = getattr(parser_sidebar, "vacancy_score_threshold", None) if parser_sidebar else None
    _sub_sl = getattr(parser_sidebar, "vacancy_min_substantive_score", None) if parser_sidebar else None
    vacancy_threshold = st.slider(
        "Минимальный vacancy_score",
        0,
        10,
        int(_thr_sl if _thr_sl is not None else 3),
    )
    vacancy_min_substantive = st.slider(
        "Мин. содержательных баллов (без email/@)",
        0,
        10,
        int(_sub_sl if _sub_sl is not None else 0),
        help="При ≥1 нужен хоть один признак помимо упоминания контакта: вакансия, зарплата, office/remote и т.д.",
    )


def split_csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def rebuild_config_inline() -> "AppConfig":
    from tg_jobs_parser.config.schema import (
        AppConfig,
        FiltersConfig,
        OutputConfig,
        SearchConfig,
        SourcesConfig,
        TelegramConfig,
    )

    base = load_config(cfg_path)
    chans = []
    for line in channel_text.splitlines():
        chans.extend(split_csv(line))
    chans = [c for c in chans if c]
    if not chans:
        chans = list(base.sources.channels)

    data = base.model_dump()
    data["sources"] = SourcesConfig(channels=chans).model_dump()
    data["search"] = SearchConfig(
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        max_days=base.search.max_days,
    ).model_dump()
    ex_lines = [x.strip() for x in exclusions_input.splitlines() if x.strip()]
    ex_kw = ex_lines if ex_lines else base.filters.exclude_keywords

    data["filters"] = FiltersConfig(
        specializations=chosen_specs if chosen_specs else base.filters.specializations,
        exclude_keywords=ex_kw,
        countries=split_csv(countries),
        cities=split_csv(cities),
        work_formats=wf if wf else base.filters.work_formats,
    ).model_dump()
    p = dict(data.get("parser", {}))
    p["vacancy_score_threshold"] = int(vacancy_threshold)
    p["vacancy_min_substantive_score"] = int(vacancy_min_substantive)
    data["parser"] = p
    data["output"] = OutputConfig(**data.get("output", {})).model_dump()
    session_file = (telethon_session_path or "").strip() or base.telegram.telethon_session_file
    data["telegram"] = TelegramConfig(
        mode=telegram_mode_sel,
        telethon_session_file=session_file,
    ).model_dump()
    return AppConfig.model_validate(data)


tab_run, tab_results = st.tabs(["Запуск", "Последний результат"])

with tab_run:
    if st.button("Запустить парсинг", type="primary"):
        try:
            cfg_inline = rebuild_config_inline()
        except ConfigLoadError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.exception(e)
            st.stop()

        with st.spinner("Парсинг..."):
            result = run_parse(cfg_inline, cli_overrides={"ui": True})
        st.session_state["last_result"] = result

        if result.web_limitation_warning:
            st.warning(result.web_limitation_warning)

        st.success(
            f"Постов: {result.posts_fetched}; вакансий после фильтров: {len(result.vacancies_all)}; "
            f"уникальных: {result.metrics.unique_vacancies}; дубликатов: {result.metrics.duplicate_count}"
        )

        rows = []
        for v in result.vacancies_unique:
            d = v.model_dump(mode="python")
            d["technologies"] = ", ".join(d["technologies"])
            d["detected_specializations"] = ", ".join(d["detected_specializations"])
            rows.append(d)
        df = pd.DataFrame(rows)
        if not df.empty:
            st.subheader("Таблица (уникальные)")
            show_cols = st.multiselect(
                "Колонки",
                options=list(df.columns),
                default=[c for c in ["id", "source_channel", "post_date", "vacancy_score", "cleaned_text"] if c in df.columns],
            )
            st.dataframe(df[show_cols], use_container_width=True, height=400)

            st.subheader("Графики (Plotly)")
            c1, c2 = st.columns(2)
            fig1 = plotly_spec_bar(result.metrics)
            if fig1:
                c1.plotly_chart(fig1, use_container_width=True)
            fig2 = plotly_grade_bar(result.metrics)
            if fig2:
                c2.plotly_chart(fig2, use_container_width=True)
            fig3 = plotly_daily_line(result.metrics)
            if fig3:
                st.plotly_chart(fig3, use_container_width=True)

        out_dir = Path(cfg_inline.output.directory)
        out_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = out_dir / cfg_inline.output.excel_filename
        json_path = out_dir / cfg_inline.output.json_filename
        export_excel(
            xlsx_path,
            result.vacancies_all,
            result.vacancies_unique,
            result.duplicates,
            result.metrics,
        )
        export_json(result, json_path)

        st.download_button(
            "Скачать Excel",
            data=xlsx_path.read_bytes(),
            file_name=xlsx_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.download_button(
            "Скачать JSON",
            data=json_path.read_text(encoding="utf-8"),
            file_name=json_path.name,
            mime="application/json",
        )

with tab_results:
    res = st.session_state.get("last_result")
    if not res:
        st.info("Сначала выполните запуск на вкладке «Запуск».")
    else:
        st.json(
            {
                "posts_fetched": res.posts_fetched,
                "unique": res.metrics.unique_vacancies,
                "duplicates": res.metrics.duplicate_count,
            }
        )
