"""Streamlit UI для локального MVP."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


def _require_streamlit_runtime() -> None:
    """
    Запуск через ``python streamlit_app.py`` не создаёт контекст Streamlit (см. ScriptRunContext).
    Нужна команда ``streamlit run …`` или ``python -m tg_jobs_parser.cli run-web``.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:
        return
    if get_script_run_ctx() is not None:
        return
    print(
        "Этот файл нельзя запускать как обычный Python-скрипт.\n\n"
        "  poetry run streamlit run tg_jobs_parser/web/streamlit_app.py\n\n"
        "или с тем же интерпретатором:\n\n"
        "  python -m tg_jobs_parser.cli run-web\n",
        file=sys.stderr,
    )
    raise SystemExit(2)


_require_streamlit_runtime()

from tg_jobs_parser.utils.asyncio_windows import install_windows_asyncio_cleanup_helpers

install_windows_asyncio_cleanup_helpers()

from tg_jobs_parser.analytics.charts import plotly_daily_line, plotly_grade_bar, plotly_spec_bar
from tg_jobs_parser.channel_search import search_channels
from tg_jobs_parser.config.loader import ConfigLoadError, load_config
from tg_jobs_parser.ai.llm_models_registry import (
    load_llm_models_registry,
    registry_signature,
    resolve_llm_models_path,
)
from tg_jobs_parser.ai.models_catalog import catalog_model_ids, label_for_model_id

_AI_CONFIG_SIG_KEY = "ai_config_signature"
_ANY_SPEC_KEY = "filter_any_specialization"
_AI_PRIMARY_MODEL_KEY = "ai_primary_model"
_AI_FALLBACK_MODELS_KEY = "ai_fallback_models"
_USE_AI_KEY = "use_ai_llm"
_IGNORE_LLM_CACHE_KEY = "ignore_llm_cache_run"
from tg_jobs_parser.config.schema import DEFAULT_SPECIALIZATIONS
from tg_jobs_parser.exporters.excel_exporter import export_excel
from tg_jobs_parser.exporters.json_exporter import export_json
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_progress import ParseProgressSnapshot
from tg_jobs_parser.parse_runner import run_parse
from tg_jobs_parser.utils.dates import parse_date, resolve_search_date_to
from tg_jobs_parser.utils.logger import setup_logging
from tg_jobs_parser.utils.text import post_id_display

st.set_page_config(page_title="Telegram Jobs MVP", layout="wide")
setup_logging()


def default_config_path() -> Path:
    cand = Path("config.yaml")
    if cand.is_file():
        return cand
    return Path(__file__).resolve().parents[2] / "config.yaml"


def _date_range_defaults(path_str: str) -> tuple[date, date]:
    """Начальные date_from / date_to из YAML; date_to без даты → сегодня."""
    t = date.today()
    try:
        p = Path(path_str)
        if p.is_file():
            c = load_config(p)
            df = parse_date(c.search.date_from)
            dt = resolve_search_date_to(c.search.date_to)
            if df:
                return df, dt
            if c.search.date_to is not None or c.search.date_from is not None:
                return t, dt
    except (ConfigLoadError, OSError, ValueError):
        pass
    return t, t


st.title("Парсер IT-вакансий из Telegram-каналов (Telethon)")

with st.sidebar:
    st.header("Источники")
    cfg_path = st.text_input("Путь к config.yaml", value=str(default_config_path()))
    channel_text = st.text_area(
        "Каналы (по строке или через запятую)",
        placeholder="python_django_jobs\nhttps://t.me/devops_jobs",
        height=120,
    )

    kw = st.text_input("Поиск каналов (best-effort, DuckDuckGo)", value="python jobs telegram channel")
    if st.button("Найти кандидатов каналов", key="btn_channel_search"):
        found, backend = search_channels(kw)
        st.session_state["channel_search_last"] = {
            "found": found,
            "backend": backend,
            "query": kw.strip(),
        }
    _ch_search = st.session_state.get("channel_search_last")
    if _ch_search:
        st.caption(f"Последний поиск ({_ch_search.get('query', '')!r}), бэкенд: {_ch_search.get('backend', '')}")
        _found = _ch_search.get("found") or []
        if _found:
            st.write(_found)
        else:
            st.info(
                "Ответ DuckDuckGo был 200, но в HTML не нашлось ссылок вида t.me/… "
                "(часто так бывает: пустая выдача, антибот или другая вёрстка). "
                "Каналы надёжнее указывать вручную выше."
            )

    _sess_default = "sessions/tg_jobs.session"
    try:
        if Path(cfg_path).is_file():
            _sess_default = load_config(cfg_path).telegram.telethon_session_file
    except ConfigLoadError:
        pass

    telethon_session_path = st.text_input(
        "Файл сессии Telethon",
        value=_sess_default,
        help="Парсинг только через Telethon (MTProto). Первая авторизация удобнее из CLI.",
    )
    st.caption(
        "Нужны TELEGRAM_API_ID / TELEGRAM_API_HASH в .env. Один раз выполните parse через CLI, чтобы создать файл сессии."
    )

    st.header("Фильтры")
    all_specs = list(DEFAULT_SPECIALIZATIONS.keys())
    any_specialization = st.checkbox(
        "Любая IT-специализация",
        key=_ANY_SPEC_KEY,
        help=(
            "Не отсекать посты по списку ролей. Для LLM (в т.ч. mode primary): "
            "не помечать вакансию как не-вакансию только из‑за PHP/Rust/… вне target list."
        ),
    )
    if any_specialization:
        st.caption("Активен режим «любая IT-роль» — multiselect ниже не влияет на прогон.")
    chosen_specs = st.multiselect(
        "Специализации (если не «любая IT»)",
        options=all_specs,
        default=["Python", "Backend", "QA"],
        disabled=any_specialization,
    )

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

    _df0, _dt0 = _date_range_defaults(cfg_path)
    date_from = st.date_input("date_from", value=_df0, key="run_date_from")
    date_to = st.date_input("date_to", value=_dt0, key="run_date_to")
    st.caption("Стартовые даты — из config.yaml (не «только сегодня»).")
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

    st.header("AI (OpenRouter)")
    _cfg_file = Path(cfg_path)
    _ai_cfg = None
    _ai_load_err: str | None = None
    try:
        if _cfg_file.is_file():
            _ai_cfg = load_config(cfg_path).ai
    except ConfigLoadError as e:
        _ai_load_err = str(e)

    if _USE_AI_KEY not in st.session_state:
        st.session_state[_USE_AI_KEY] = bool(getattr(_ai_cfg, "enabled", False))

    use_ai = st.checkbox(
        "Использовать LLM для разбора текста",
        key=_USE_AI_KEY,
        help="Нужен OPENROUTER_API_KEY в .env. Включение здесь действует на этот запуск, даже если в config.yaml ai.enabled: false.",
    )
    _cfg_enabled = bool(getattr(_ai_cfg, "enabled", False))
    if not _cfg_enabled and not use_ai:
        st.caption("В config.yaml `ai.enabled: false` — включите чекбокс выше для LLM в этом прогоне.")
    elif not _cfg_enabled and use_ai:
        st.caption("LLM включён в UI; в config.yaml по-прежнему `ai.enabled: false`.")

    if not _cfg_file.is_file():
        st.warning(f"config не найден: `{cfg_path}` — нужен `llm_models.yaml` в корне проекта.")
    elif _ai_load_err:
        st.error(f"Ошибка чтения AI из config: {_ai_load_err}")

    _llm_registry = None
    _llm_path: Path | None = None
    try:
        if _cfg_file.is_file():
            _llm_path = resolve_llm_models_path(
                cfg_path,
                explicit=getattr(_ai_cfg, "llm_models_path", None) if _ai_cfg else None,
            )
            _llm_registry = load_llm_models_registry(cfg_path)
    except Exception as e:
        if not _ai_load_err:
            _ai_load_err = str(e)
    _catalog = _llm_registry.catalog() if _llm_registry else []
    _model_ids = catalog_model_ids(_catalog)
    _cfg_model = (getattr(_ai_cfg, "model", None) or "").strip() if _ai_cfg else ""
    _cfg_fallbacks = list(getattr(_ai_cfg, "fallback_models", [])) if _ai_cfg else []
    _sig = (
        registry_signature(_llm_path, _llm_registry) + f"|{cfg_path}|{_cfg_model}|{'|'.join(_cfg_fallbacks)}"
        if _llm_registry and _llm_path
        else f"{cfg_path}|{_cfg_model}"
    )

    if st.button("Сбросить выбор моделей к config.yaml", key="btn_reset_ai_models"):
        st.session_state.pop(_AI_CONFIG_SIG_KEY, None)
        st.session_state.pop(_AI_PRIMARY_MODEL_KEY, None)
        st.session_state.pop(_AI_FALLBACK_MODELS_KEY, None)
        st.session_state[_USE_AI_KEY] = _cfg_enabled

    if _model_ids and st.session_state.get(_AI_CONFIG_SIG_KEY) != _sig:
        st.session_state[_AI_CONFIG_SIG_KEY] = _sig
        st.session_state[_AI_PRIMARY_MODEL_KEY] = (
            _cfg_model if _cfg_model in _model_ids else _model_ids[0]
        )
        st.session_state[_AI_FALLBACK_MODELS_KEY] = [
            m
            for m in _cfg_fallbacks
            if m in _model_ids and m != st.session_state[_AI_PRIMARY_MODEL_KEY]
        ]

    if _llm_path and _llm_path.is_file():
        st.caption(f"**Каталог моделей:** `{_llm_path.resolve()}`")
    if _cfg_model and _catalog:
        st.caption(f"**Дефолт в config.yaml:** {label_for_model_id(_catalog, _cfg_model)}")

    def _fmt_model(mid: str) -> str:
        return label_for_model_id(_catalog, mid)

    ai_model_choice = st.selectbox(
        "Основная модель",
        options=_model_ids or [""],
        format_func=_fmt_model,
        key=_AI_PRIMARY_MODEL_KEY,
        disabled=not _model_ids,
        help="Список из llm_models.yaml. Выбор действует на этот запуск.",
    )
    _fallback_opts = [m for m in _model_ids if m != ai_model_choice]
    ai_fallback_models = st.multiselect(
        "Fallback-модели (порядок = приоритет)",
        options=_fallback_opts,
        format_func=_fmt_model,
        key=_AI_FALLBACK_MODELS_KEY,
        disabled=not _fallback_opts,
    )
    ignore_llm_cache = st.checkbox(
        "Не использовать кэш LLM в этом прогоне",
        key=_IGNORE_LLM_CACHE_KEY,
        help="Все посты пойдут в API (если не отсечены по score). Кэш на диске не удаляется.",
    )
    if use_ai and ai_model_choice:
        _fb_suffix = ""
        if ai_fallback_models:
            if len(ai_fallback_models) > 3:
                _fb_suffix = f" → fallback: {', '.join(ai_fallback_models[:3])}…"
            else:
                _fb_suffix = f" → fallback: {', '.join(ai_fallback_models)}"
        _cache_note = "без кэша" if ignore_llm_cache else "с кэшем"
        st.caption(
            f"**В прогоне:** {_fmt_model(ai_model_choice)}{_fb_suffix} ({_cache_note})"
        )


def split_csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


def _vacancies_to_display_df(vacancies: list[Vacancy]) -> pd.DataFrame:
    rows = []
    for v in vacancies:
        d = v.model_dump(mode="python")
        d["id"] = post_id_display(str(d.get("id") or ""))
        d["technologies"] = ", ".join(d.get("technologies") or [])
        d["detected_specializations"] = ", ".join(d.get("detected_specializations") or [])
        d["application_urls"] = ", ".join(d.get("application_urls") or [])
        rows.append(d)
    df = pd.DataFrame(rows)
    if not df.empty:
        df.index = pd.RangeIndex(start=1, stop=len(df) + 1)
        for link_col in ("post_url", "source_url"):
            if link_col in df.columns:
                df[link_col] = df[link_col].apply(
                    lambda x: str(x).strip() if x is not None and str(x).strip() else None
                )
    return df


def _vacancy_table_column_config(columns: list[str]) -> dict:
    """``LinkColumn`` для URL — иначе Streamlit показывает их обычным текстом."""
    cfg: dict = {}
    if "detected_salary_avg" in columns:
        cfg["detected_salary_avg"] = st.column_config.NumberColumn(
            "Средняя зарплата",
            format="%.0f",
        )
    if "salary_currency" in columns:
        cfg["salary_currency"] = st.column_config.TextColumn("Валюта")
    if "work_format" in columns:
        cfg["work_format"] = st.column_config.TextColumn("Формат работы")
    if "post_url" in columns:
        cfg["post_url"] = st.column_config.LinkColumn(
            "Пост в Telegram",
            help="Клик откроет пост в Telegram.",
            display_text="Открыть пост",
        )
    if "source_url" in columns:
        cfg["source_url"] = st.column_config.LinkColumn(
            "Канал",
            display_text="Канал",
        )
    if "application_urls" in columns:
        cfg["application_urls"] = st.column_config.TextColumn("Ссылки отклика")
    return cfg


def _render_live_progress(
    snap: ParseProgressSnapshot,
    *,
    progress_bar: st.delta_generator.DeltaGenerator,
    status_box: st.delta_generator.DeltaGenerator,
    table_box: st.delta_generator.DeltaGenerator,
    partial_caption: st.delta_generator.DeltaGenerator | None = None,
) -> None:
    ratio = snap.posts_processed / snap.posts_total if snap.posts_total else 0.0
    progress_bar.progress(min(1.0, ratio), text=f"Посты {snap.posts_processed}/{snap.posts_total}")
    lines = [
        f"**Вакансий найдено (промежуточно):** {snap.vacancies_found}",
        f"Фаза: `{snap.phase}`",
    ]
    if snap.ai_llm_disabled:
        lines.append(
            f"**LLM отключён** (дальше только rules): {snap.ai_llm_disabled_reason or 'rate limit'}"
        )
    if snap.ai_skipped_score:
        lines.append(
            f"**Без LLM (низкий vacancy_score):** {snap.ai_skipped_score}"
        )
    if snap.ai_tokens_in or snap.ai_tokens_out:
        total_tok = snap.ai_tokens_in + snap.ai_tokens_out
        tok_label = (
            "Итого токенов LLM за прогон"
            if snap.phase == "post_processing_done"
            else "Токены LLM (на сейчас)"
        )
        lines.append(
            f"**{tok_label}:** input={snap.ai_tokens_in}, "
            f"output={snap.ai_tokens_out}, всего={total_tok}"
        )
    status_box.markdown("\n\n".join(lines))
    if snap.vacancies:
        df = _vacancies_to_display_df(snap.vacancies)
        prefer = [
            c
            for c in [
                "post_url",
                "source_channel",
                "post_date",
                "detected_salary_avg",
                "salary_currency",
                "work_format",
                "detected_specializations",
                "application_urls",
                "cleaned_text",
            ]
            if c in df.columns
        ]
        cols = prefer if prefer else list(df.columns)
        table_box.dataframe(
            df[cols],
            width="stretch",
            height=min(520, 80 + 36 * len(df)),
            column_config=_vacancy_table_column_config(cols),
        )
    else:
        table_box.caption("Вакансий пока нет — таблица обновится по мере обработки.")
    if partial_caption is not None and snap.extra.get("partial_path"):
        partial_caption.caption(f"Сохранён батч: `{snap.extra['partial_path']}`")


@st.dialog("Подробная вакансия", width="large")
def _show_vacancy_dialog(v, index_1based: int) -> None:
    """Широкая модалка с полным текстом вакансии и быстрыми ссылками."""
    st.markdown(f"### №{index_1based} — @{v.source_channel}")

    btn_cols = st.columns([1, 1])
    if v.post_url:
        btn_cols[0].link_button("Открыть пост в Telegram", v.post_url, type="primary")
    if v.source_url:
        btn_cols[1].link_button(f"Открыть канал @{v.source_channel}", v.source_url)

    meta: list[str] = []
    if v.post_date:
        meta.append(f"Дата: {v.post_date.strftime('%Y-%m-%d %H:%M')}")
    if v.detected_salary_avg:
        meta.append(
            f"Зарплата: ~{v.detected_salary_avg:,.0f} {v.salary_currency or ''}".strip()
        )
    if v.work_format:
        meta.append(f"Формат: {v.work_format}")
    if v.detected_grade and v.detected_grade != "unknown":
        meta.append(f"Грейд: {v.detected_grade}")
    loc_parts = [x for x in (v.country, v.city) if x]
    if loc_parts:
        meta.append("Локация: " + " / ".join(loc_parts))
    if v.company:
        meta.append(f"Компания: {v.company}")
    if v.contact:
        meta.append(f"Контакт: {v.contact}")
    if getattr(v, "application_urls", None):
        meta.append("Ссылки отклика: " + ", ".join(v.application_urls[:5]))
    if meta:
        st.caption(" • ".join(meta))

    if v.detected_specializations:
        st.markdown("**Специализации:** " + ", ".join(v.detected_specializations))
    if v.technologies:
        st.markdown("**Технологии:** " + ", ".join(v.technologies))

    st.divider()
    st.markdown("**Полный текст:**")
    st.write(v.cleaned_text or v.raw_text or "_(пусто)_")


def rebuild_config_inline() -> "AppConfig":
    from tg_jobs_parser.config.schema import (
        AiConfig,
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

    if any_specialization:
        _specs: list[str] = []
        _any_spec = True
    elif chosen_specs:
        _specs = list(chosen_specs)
        _any_spec = False
    else:
        _specs = list(base.filters.specializations)
        _any_spec = bool(getattr(base.filters, "any_specialization", False))

    data["filters"] = FiltersConfig(
        specializations=_specs,
        any_specialization=_any_spec,
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
    data["telegram"] = TelegramConfig(telethon_session_file=session_file).model_dump()
    ai_base = dict(data.get("ai", {}))
    if use_ai:
        ai_base["enabled"] = True
        if ai_model_choice:
            ai_base["model"] = ai_model_choice
        if ai_fallback_models:
            ai_base["fallback_models"] = [
                m for m in ai_fallback_models if m != ai_model_choice
            ]
        if ignore_llm_cache:
            ai_base["use_cache"] = False
    else:
        ai_base["enabled"] = False
    data["ai"] = AiConfig(**ai_base).model_dump()
    return AppConfig.model_validate(data)


tab_run, tab_results = st.tabs(["Запуск", "Последний результат"])

with tab_run:
    st.subheader("Прогресс в реальном времени")
    live_progress = st.progress(0.0, text="Нажмите «Запустить парсинг»")
    live_status = st.empty()
    live_table = st.empty()
    live_partial = st.empty()

    # Виджеты (multiselect колонок и др.) вызывают полный rerun скрипта; результат должен жить
    # вне «if st.button», иначе после любого изменения таблица пропадает (кнопка в новом прогоне False).
    if st.button("Запустить парсинг", type="primary"):
        try:
            cfg_inline = rebuild_config_inline()
        except ConfigLoadError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.exception(e)
            st.stop()

        def _on_progress(snap: ParseProgressSnapshot) -> None:
            _render_live_progress(
                snap,
                progress_bar=live_progress,
                status_box=live_status,
                table_box=live_table,
                partial_caption=live_partial,
            )

        result = run_parse(
            cfg_inline,
            cli_overrides={"ui": True},
            progress_callback=_on_progress,
        )
        st.session_state["last_result"] = result

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
        st.session_state["download_xlsx_bytes"] = xlsx_path.read_bytes()
        st.session_state["download_json_text"] = json_path.read_text(encoding="utf-8")

    result = st.session_state.get("last_result")
    if result is None:
        st.caption("Нажмите «Запустить парсинг», чтобы загрузить данные.")
    else:
        if result.parse_warnings:
            st.warning(result.parse_warnings)

        ai_stats = (result.run_params or {}).get("ai_stats")
        if ai_stats:
            tin = int(ai_stats.get("tokens_in") or 0)
            tout = int(ai_stats.get("tokens_out") or 0)
            tsum = int(ai_stats.get("tokens_total") or tin + tout)
            t1, t2, t3 = st.columns(3)
            t1.metric("AI tokens (input)", f"{tin:,}".replace(",", " "))
            t2.metric("AI tokens (output)", f"{tout:,}".replace(",", " "))
            t3.metric("AI tokens (всего)", f"{tsum:,}".replace(",", " "))
            skipped = int(ai_stats.get("skipped_score") or 0)
            _ai_rp = (result.run_params or {}).get("ai") or {}
            _model_rp = (_ai_rp.get("model") or "").strip()
            _model_line = f"Модель прогона: `{_model_rp}`.\n\n" if _model_rp else ""
            st.info(
                f"{_model_line}"
                f"AI: вызовов {ai_stats.get('calls', 0)}, кэш {ai_stats.get('cache_hits', 0)}, "
                f"без LLM (низкий score) {skipped}, "
                f"отсечено не-вакансий {ai_stats.get('dropped_not_job', 0)}, "
                f"пропуск rate-limit {ai_stats.get('rate_limit_skipped', 0)}, "
                f"ошибок {ai_stats.get('errors', 0)}."
            )

        st.success(
            f"Постов: {result.posts_fetched}; вакансий после фильтров: {len(result.vacancies_all)}; "
            f"уникальных: {result.metrics.unique_vacancies}; дубликатов: {result.metrics.duplicate_count}"
        )

        df = _vacancies_to_display_df(result.vacancies_unique)
        # Нумерация строк в st.dataframe берётся из индекса — сдвигаем на 1, чтобы строки
        # отображались с «1», а не с «0».
        if not df.empty:
            df.index = pd.RangeIndex(start=1, stop=len(df) + 1)
        if not df.empty:
            st.subheader("Таблица (уникальные)")
            default_cols = [
                c
                for c in [
                    "post_url",
                    "source_channel",
                    "post_date",
                    "detected_salary_avg",
                    "salary_currency",
                    "work_format",
                    "vacancy_score",
                    "cleaned_text",
                ]
                if c in df.columns
            ]
            show_cols = st.multiselect(
                "Колонки",
                options=list(df.columns),
                default=default_cols or list(df.columns)[:8],
                key="vacancy_unique_columns_v4",
            )
            st.caption(
                "Кликните по строке таблицы — откроется широкое окно с полным текстом вакансии и кнопками-ссылками."
            )
            if show_cols:
                _col_cfg = _vacancy_table_column_config(show_cols)
                table_event = st.dataframe(
                    df[show_cols],
                    width="stretch",
                    height=400,
                    column_config=_col_cfg or None,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="vacancy_unique_dataframe",
                )

                # event.selection.rows возвращает позиционные индексы DataFrame (0-based),
                # которые совпадают с позицией в result.vacancies_unique.
                sel_rows: list[int] = []
                if table_event is not None and hasattr(table_event, "selection"):
                    sel_rows = list(getattr(table_event.selection, "rows", None) or [])

                last_seen = st.session_state.get("_vacancy_dialog_last_row")
                if sel_rows:
                    pos = sel_rows[0]
                    if 0 <= pos < len(result.vacancies_unique) and pos != last_seen:
                        st.session_state["_vacancy_dialog_last_row"] = pos
                        _show_vacancy_dialog(result.vacancies_unique[pos], pos + 1)
                else:
                    st.session_state["_vacancy_dialog_last_row"] = None
            else:
                st.info("Выберите хотя бы одну колонку.")
        else:
            st.info("Нет строк для таблицы уникальных вакансий (после фильтров).")

        st.subheader("Графики (Plotly)")
        c1, c2 = st.columns(2)
        fig1 = plotly_spec_bar(result.metrics)
        if fig1:
            c1.plotly_chart(fig1, width="stretch")
        fig2 = plotly_grade_bar(result.metrics)
        if fig2:
            c2.plotly_chart(fig2, width="stretch")
        fig3 = plotly_daily_line(result.metrics)
        if fig3:
            st.plotly_chart(fig3, width="stretch")

        rp = result.run_params or {}
        out_rp = rp.get("output") or {}
        xlsx_name = out_rp.get("excel_filename", "vacancies.xlsx")
        json_name = out_rp.get("json_filename", "vacancies.json")

        xlsx_bytes = st.session_state.get("download_xlsx_bytes")
        json_text = st.session_state.get("download_json_text")
        if xlsx_bytes:
            st.download_button(
                "Скачать Excel",
                data=xlsx_bytes,
                file_name=xlsx_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_xlsx",
            )
        if json_text:
            st.download_button(
                "Скачать JSON",
                data=json_text,
                file_name=json_name,
                mime="application/json",
                key="dl_json",
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
