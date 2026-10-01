"""Оркестрация: Telethon → извлечение → фильтры → дедуп → метрики."""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TYPE_CHECKING
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry import context as otel_context
from opentelemetry import trace

from tg_jobs_parser.analytics.metrics_builder import build_metrics
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.deduplication.deduplicator import VacancyDeduplicator
from tg_jobs_parser.extraction.company_extractor import extract_company
from tg_jobs_parser.extraction.contact_extractor import extract_contact
from tg_jobs_parser.extraction.grade_extractor import extract_grade
from tg_jobs_parser.extraction.location_extractor import detect_work_format, extract_country_city
from tg_jobs_parser.extraction.normalizer import clean_display_text, normalize_for_match
from tg_jobs_parser.extraction.salary_extractor import extract_salary
from tg_jobs_parser.extraction.salary_period import (
    apply_hourly_currency_from_text,
    sanitize_salary_currency_period,
)
from tg_jobs_parser.extraction.specialization_extractor import extract_specializations
from tg_jobs_parser.extraction.technology_extractor import extract_technologies
from tg_jobs_parser.extraction.vacancy_detector import compute_vacancy_score
from tg_jobs_parser.filters.vacancy_filters import apply_post_filters
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_cancel import (
    ParseCancellationToken,
    ParseCancelled,
    install_sigint_cancel_handler,
)
from tg_jobs_parser.parse_progress import (
    ParseProgressCallback,
    ParseProgressSnapshot,
    clear_partial_batches,
    save_partial_batch,
)
from tg_jobs_parser.parsers.base import BaseChannelParser, RawPost
from tg_jobs_parser.parsers.channel_username import normalize_channel_username
from tg_jobs_parser.utils.dates import (
    clamp_range_days,
    resolve_search_date_from,
    resolve_search_date_to,
)
from tg_jobs_parser.utils.env_loader import try_load_dotenv
from tg_jobs_parser.fx.vacancy_rub import enrich_vacancies_salary_rub
from tg_jobs_parser.utils.hashing import vacancy_content_id
from tg_jobs_parser.utils.sorting import sort_vacancies_for_report
from tg_jobs_parser.utils.logger import get_logger
from tg_jobs_parser.utils.thread_pool import DaemonThreadPoolExecutor

if TYPE_CHECKING:
    from tg_jobs_parser.ai.enricher import AIEnrichmentService

_LOG = get_logger(__name__)
_TRACER = trace.get_tracer(__name__)


def _channel_parser(
    config: AppConfig,
    *,
    cancellation: ParseCancellationToken | None = None,
) -> BaseChannelParser:
    """Единственный источник сообщений — Telethon (MTProto с файлом сессии)."""
    from tg_jobs_parser.parsers.telegram_telethon import TelegramTelethonParser, telethon_import_error

    if err := telethon_import_error():
        raise RuntimeError(
            "Нужен Telethon: poetry install (и при необходимости --extras telethon). "
            "Запускайте через poetry run python -m tg_jobs_parser.cli …"
        ) from err
    return TelegramTelethonParser(config, cancellation=cancellation)


def _extract_employment(text: str) -> str | None:
    t = normalize_for_match(text)
    if "full-time" in t or "фуллтайм" in t or "полная занятость" in t:
        return "full-time"
    if "part-time" in t or "part time" in t or "частичная" in t:
        return "part-time"
    if "contract" in t or "контракт" in t or "контрактор" in t:
        return "contract"
    return None


def _extract_experience(text: str) -> str | None:
    m = re.search(r"\b(\d+\+?\s*(?:лет|years?|y\/o)|опыт\s*от\s*\d+[\s\-]*\d*\s*(?:лет|years)?)\b", text, re.I)
    if m:
        return m.group(1).strip()
    if re.search(r"\bno\s+experience\b|без\s+опыт", text, re.I):
        return "no experience"
    return None


def raw_post_to_vacancy_rules(
    post: RawPost,
    config: AppConfig,
    *,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
) -> Vacancy:
    """
    Rule-based извлечение (без LLM).

    Словари можно передать заранее (``spec_map``/``tech_list``/``wf_map``) — это критично
    при пакетной обработке, иначе на каждый пост они пересобираются заново.
    """
    raw = post.text
    cleaned = clean_display_text(raw)
    score, _, substantive = compute_vacancy_score(raw)

    if spec_map is None:
        spec_map = config.merged_specializations()
    if tech_list is None:
        tech_list = config.merged_technologies()
    if wf_map is None:
        wf_map = config.merged_work_format_synonyms()

    specs = extract_specializations(raw, spec_map)
    salary = extract_salary(raw)
    grade = extract_grade(raw)
    country, city = extract_country_city(raw)
    wf = detect_work_format(raw, wf_map)
    contact = extract_contact(raw)
    technologies = extract_technologies(raw, tech_list)
    company = extract_company(raw)

    ch = normalize_channel_username(post.channel_username)
    source_post_key = f"{ch}-{post.post_id}"
    vid = vacancy_content_id(cleaned)

    source_url = f"https://t.me/s/{ch}"

    avg = salary.avg_value
    if salary.min_value is not None and salary.max_value is not None:
        avg = (salary.min_value + salary.max_value) / 2

    vacancy = Vacancy(
        id=vid,
        source_post_key=source_post_key,
        source_channel=ch,
        source_url=source_url,
        post_url=post.post_url,
        post_date=post.post_date,
        raw_text=raw,
        cleaned_text=cleaned,
        detected_specializations=specs,
        detected_grade=grade,
        detected_salary_min=salary.min_value,
        detected_salary_max=salary.max_value,
        detected_salary_avg=avg,
        salary_currency=salary.currency,
        work_format=wf,
        country=country,
        city=city,
        company=company,
        contact=contact,
        technologies=technologies,
        required_experience=_extract_experience(raw),
        employment_type=_extract_employment(raw),
        vacancy_score=score,
        vacancy_substantive_score=substantive,
        is_duplicate=False,
        duplicate_group_id=None,
        duplicate_reason=None,
        parsed_at=datetime.now(timezone.utc),
    )
    apply_hourly_currency_from_text(vacancy)
    sanitize_salary_currency_period(vacancy)
    return vacancy


def raw_post_to_vacancy(
    post: RawPost,
    config: AppConfig,
    *,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
) -> Vacancy:
    """Алиас rule-based извлечения (обратная совместимость)."""
    return raw_post_to_vacancy_rules(
        post, config, spec_map=spec_map, tech_list=tech_list, wf_map=wf_map
    )


def _build_parse_result(
    *,
    config: AppConfig,
    cli_overrides: dict | None,
    all_warnings: list[str],
    posts_fetched: int,
    candidates: list[Vacancy],
    unique: list[Vacancy],
    dups: list[Vacancy],
    dedup_elapsed_sec: float,
    ai_service: AIEnrichmentService | None,
    posts_processed: int = 0,
    fetch_elapsed_sec: float = 0.0,
    process_elapsed_sec: float = 0.0,
) -> ParseResult:
    _LOG.info(
        "Дедупликация: %s уникальных, %s дубликатов за %.1f с.",
        len(unique),
        len(dups),
        dedup_elapsed_sec,
    )

    sort_vacancies_for_report(candidates)
    sort_vacancies_for_report(unique)
    sort_vacancies_for_report(dups)

    if config.fx.enabled:
        enrich_started = time.monotonic()
        enrich_vacancies_salary_rub(
            unique,
            default_currency_if_missing=config.fx.default_currency_if_missing,
            request_timeout_seconds=config.fx.request_timeout_seconds,
        )
        _LOG.info(
            "Курсы для метрик прогона: %s вакансий за %.1f с.",
            len(unique),
            time.monotonic() - enrich_started,
        )

    metrics = build_metrics(
        vacancies_all=candidates,
        vacancies_unique=unique,
        duplicates=dups,
        posts_collected=posts_fetched,
    )
    metrics.warnings.extend(all_warnings)

    posts_skipped = max(0, posts_processed - len(candidates))
    total_elapsed_sec = fetch_elapsed_sec + process_elapsed_sec + dedup_elapsed_sec

    run_params = config.to_serializable_dict()
    run_params["parse_summary"] = {
        "total_elapsed_seconds": round(total_elapsed_sec, 1),
        "fetch_elapsed_seconds": round(fetch_elapsed_sec, 1),
        "process_elapsed_seconds": round(process_elapsed_sec, 1),
        "dedup_elapsed_seconds": round(dedup_elapsed_sec, 1),
        "posts_fetched": posts_fetched,
        "posts_processed": posts_processed,
        "posts_skipped": posts_skipped,
        "vacancy_candidates": len(candidates),
    }
    if ai_service is not None:
        run_params["ai_stats"] = ai_service.stats.to_dict(
            catalog=config.llm_model_catalog,
            primary_model=config.ai.model,
        )
    if cli_overrides:
        run_params["cli_overrides"] = cli_overrides

    parse_warnings = "; ".join(all_warnings) if all_warnings else None

    return ParseResult(
        run_params=run_params,
        posts_fetched=posts_fetched,
        parse_warnings=parse_warnings,
        vacancies_all=candidates,
        vacancies_unique=unique,
        duplicates=dups,
        metrics=metrics,
    )


def _process_posts_parallel(
    all_posts: list[RawPost],
    *,
    workers: int,
    pool_cls: type[ThreadPoolExecutor],
    process_one,
    cancel: ParseCancellationToken | None,
    batch_step: int,
    emit_progress,
):
    """Параллельная пост-обработка с проверкой отмены между завершёнными задачами."""
    processed = 0
    with pool_cls(max_workers=workers, thread_name_prefix="post-proc") as ex:
        # copy_context: воркеры наследуют текущий OTel-span (LLM-вызовы — дети
        # parse.process_posts, а не отдельные трассы); один Context на задачу.
        futures = {
            ex.submit(contextvars.copy_context().run, process_one, post): post
            for post in all_posts
        }
        pending = set(futures.keys())
        while pending:
            if cancel and cancel.is_cancelled():
                for fut in pending:
                    fut.cancel()
                ex.shutdown(wait=False, cancel_futures=True)
                raise ParseCancelled()
            done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for fut in done:
                processed += 1
                v = fut.result()
                yield processed, v
                if processed % batch_step == 0 or processed == len(all_posts):
                    emit_progress(processed)


def _end_span(span: trace.Span, ctx_token) -> None:
    otel_context.detach(ctx_token)
    span.end()


def _api_run(cli_overrides: dict | None) -> bool:
    """Прогон через REST API — служебные строки не дублируем в parse_warnings (есть run_params / UI)."""
    return bool(cli_overrides and cli_overrides.get("api"))


def run_parse(
    config: AppConfig,
    cli_overrides: dict | None = None,
    *,
    progress_callback: ParseProgressCallback | None = None,
    progress_every_n_posts: int | None = None,
    cancel: ParseCancellationToken | None = None,
    vacancy_batch_sink: Callable[[list[Vacancy]], None] | None = None,
) -> ParseResult:
    """
    Основной сценарий MVP.

    ``cli_overrides`` — простой dict для сериализации в ``run_params`` (опционально).
    ``vacancy_batch_sink`` — опциональный колбэк: вызывается с очередной порцией
    уникальных вакансий каждые ``parser.db_flush_every_posts`` обработанных постов,
    при накоплении ``parser.db_flush_pending_vacancies`` незаписанных уникальных
    (что наступит раньше) и в конце прогона — чтобы сохранять вакансии в БД по ходу
    обработки, а не только в конце.
    """
    try_load_dotenv()

    if cancel is not None:
        install_sigint_cancel_handler(cancel, log=_LOG.warning)

    channel_fetcher = _channel_parser(config, cancellation=cancel)

    date_from = resolve_search_date_from(config.search.date_from)
    date_to = resolve_search_date_to(config.search.date_to)

    all_warnings: list[str] = []

    if date_from and date_to:
        span = (date_to - date_from).days + 1
        cap = min(config.search.max_days, 365)
        df_clamped, dt_clamped = clamp_range_days(date_from, date_to, cap)
        if span > cap:
            all_warnings.append(
                f"Запрошенный период {span} дн. обрезан до {cap} дн. "
                f"({df_clamped.isoformat()} — {dt_clamped.isoformat()})."
            )
        config = config.model_copy(
            update={
                "search": config.search.model_copy(
                    update={
                        "date_from": df_clamped.isoformat(),
                        "date_to": dt_clamped.isoformat(),
                    }
                )
            }
        )
        date_from, date_to = df_clamped, dt_clamped

    if not _api_run(cli_overrides):
        all_warnings.append(
            "Telegram: Telethon (MTProto). Убедитесь, что активна сессия и заданы "
            "TELEGRAM_API_ID / TELEGRAM_API_HASH."
        )

    # Предвычисляем все словари один раз — иначе они пересобирались бы на каждом из тысяч постов.
    specs_map = config.merged_specializations()
    tech_list = config.merged_technologies()
    wf_map = config.merged_work_format_synonyms()
    exclude_kw = config.merged_exclude_keywords()
    threshold = config.parser.vacancy_score_threshold

    posts_fetched = 0
    candidates: list[Vacancy] = []
    batch_step = progress_every_n_posts or config.parser.progress_batch_posts
    partial_dir: Path | None = None
    partial_run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if config.output.save_progress_batches:
        partial_dir = Path(config.output.directory) / config.output.partial_batches_subdirectory
        removed = clear_partial_batches(partial_dir)
        if removed:
            _LOG.info("Partial-батчи: удалено %s старых файлов из %s", removed, partial_dir)

    channels_unique: list[str] = []
    for ch in config.sources.channels:
        u = normalize_channel_username(ch)
        if u and u not in channels_unique:
            channels_unique.append(u)

    # Каналы опрашиваются параллельно в рамках одной Telethon-сессии (см. parser.channel_concurrency).
    # Результат — список (username, posts, warning) в произвольном порядке: пост-обработка и так
    # сортирует выдачу по дате, поэтому порядок каналов значения не имеет.
    fetch_started = time.monotonic()
    try:
        if cancel:
            cancel.check()
        with _TRACER.start_as_current_span(
            "parse.fetch_posts", attributes={"parse.channels": len(channels_unique)}
        ) as fetch_span:
            fetch_results = channel_fetcher.fetch_posts_many(channels_unique)
            fetch_span.set_attribute(
                "parse.posts_fetched", sum(len(posts) for _, posts, _ in fetch_results)
            )
        if cancel:
            cancel.check()
    except ParseCancelled:
        _LOG.warning("Выборка прервана до завершения обхода каналов.")
        all_warnings.append("Прогон прерван пользователем во время выборки постов.")
        fetch_elapsed = time.monotonic() - fetch_started
        return _build_parse_result(
            config=config,
            cli_overrides=cli_overrides,
            all_warnings=all_warnings,
            posts_fetched=0,
            candidates=[],
            unique=[],
            dups=[],
            dedup_elapsed_sec=0.0,
            ai_service=None,
            fetch_elapsed_sec=fetch_elapsed,
        )
    fetch_elapsed = time.monotonic() - fetch_started

    all_posts: list[RawPost] = []
    for ch, posts, warn in fetch_results:
        if warn:
            all_warnings.append(warn)
            _LOG.warning("Канал @%s: %s", ch, warn)
        posts_fetched += len(posts)
        all_posts.extend(posts)

    _LOG.info(
        "Выборка завершена: %s постов с %s каналов за %.1f с.",
        posts_fetched,
        len(channels_unique),
        fetch_elapsed,
    )

    ai_service: AIEnrichmentService | None = None
    if config.ai.enabled:
        from tg_jobs_parser.ai.enricher import AIEnrichmentService

        try:
            ai_service = AIEnrichmentService(config)
            ai_service.begin_batch(len(all_posts))
            if not _api_run(cli_overrides):
                all_warnings.append(
                    f"AI: OpenRouter, модель {config.ai.model}"
                    + (
                        f", fallback [{', '.join(config.ai.resolved_fallback_models())}]"
                        if config.ai.resolved_fallback_models()
                        else ""
                    )
                    + f", режим {config.ai.mode}, concurrency={config.ai.concurrency}."
                )
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("openai").setLevel(logging.WARNING)
            _LOG.info(
                "AI-обогащение включено (до дедупликации): модель %s, кэш %s (%s), concurrency=%s.",
                config.ai.model,
                "вкл" if config.ai.use_cache else "выкл",
                config.ai.cache_backend,
                config.ai.concurrency,
            )
        except Exception as e:
            all_warnings.append(f"AI не запущен: {e}")
            _LOG.error("AI не инициализирован: %s", e)

    process_elapsed = 0.0
    deduplicator = VacancyDeduplicator(config)
    dedup_elapsed = 0.0
    db_flush_step = max(1, config.parser.db_flush_every_posts)
    db_flush_pending = max(1, config.parser.db_flush_pending_vacancies)
    pending_uniques: list[Vacancy] = []  # уникальные вакансии, ещё не ушедшие в sink
    process_span: trace.Span | None = None
    process_ctx_token = None
    try:

        def _register_candidate(v: Vacancy) -> None:
            """Кандидат прошёл фильтры — сразу прогоняем через инкрементальный дедуп."""
            nonlocal dedup_elapsed
            candidates.append(v)
            started = time.monotonic()
            new_unique, _ = deduplicator.add((v,))
            dedup_elapsed += time.monotonic() - started
            pending_uniques.extend(new_unique)

        def _flush_pending() -> None:
            """
            Отдать накопленные уникальные вакансии в ``vacancy_batch_sink``.

            Ошибки записи глотаем: прогон не должен падать из-за БД — незаписанные
            вакансии остаются в pending и будут предложены повторно на следующем
            флаше / финальным UPSERT в конце прогона.
            """
            if vacancy_batch_sink is None or not pending_uniques:
                return
            try:
                vacancy_batch_sink(list(pending_uniques))
            except ParseCancelled:
                raise
            except Exception as e:
                _LOG.warning(
                    "Промежуточная запись %s вакансий в БД не удалась: %s — повторим позже.",
                    len(pending_uniques),
                    e,
                )
                return
            pending_uniques.clear()

        def _process_one(post: RawPost) -> Vacancy | None:
            if cancel and cancel.is_cancelled():
                return None
            v = raw_post_to_vacancy_rules(
                post, config, spec_map=specs_map, tech_list=tech_list, wf_map=wf_map
            )
            if ai_service is not None:
                if cancel and cancel.is_cancelled():
                    return None
                v = ai_service.enrich(v, cancel=cancel)
                if v is None:
                    return None
            elif v.vacancy_score < threshold:
                return None
            if not apply_post_filters(v, config, specs_map, exclude_keywords=exclude_kw):
                return None
            return v

        def _emit_progress(posts_processed: int, *, phase: str = "processing") -> None:
            if not progress_callback and not partial_dir:
                return
            rl = ai_service._client.rate_limiter if ai_service is not None else None
            snap = ParseProgressSnapshot(
                posts_processed=posts_processed,
                posts_total=len(all_posts),
                vacancies_found=len(candidates),
                vacancies=list(candidates),
                phase=phase,
                ai_llm_disabled=bool(rl and rl.llm_disabled),
                ai_llm_disabled_reason=rl.llm_disabled_reason if rl else None,
                ai_tokens_in=ai_service.stats.tokens_in if ai_service else 0,
                ai_tokens_out=ai_service.stats.tokens_out if ai_service else 0,
                ai_skipped_score=ai_service.stats.skipped_score if ai_service else 0,
            )
            if partial_dir and candidates:
                p = save_partial_batch(partial_dir, snap, run_id=partial_run_id)
                snap.extra["partial_path"] = str(p)
            if progress_callback:
                progress_callback(snap)

        # Пост-обработка: regex (+ опционально LLM в пуле потоков).
        if ai_service is not None:
            workers = max(1, min(config.ai.concurrency, 16))
        else:
            workers = max(2, min(8, (os.cpu_count() or 4)))

        pool_cls = DaemonThreadPoolExecutor if ai_service is not None else ThreadPoolExecutor
        process_span = _TRACER.start_span(
            "parse.process_posts",
            attributes={
                "parse.posts_total": len(all_posts),
                "parse.workers": workers,
                "parse.ai_enabled": ai_service is not None,
            },
        )
        process_ctx_token = otel_context.attach(trace.set_span_in_context(process_span))
        process_started = time.monotonic()
        if len(all_posts) < 200 and ai_service is None:
            for i, post in enumerate(all_posts, 1):
                if cancel:
                    cancel.check()
                v = _process_one(post)
                if v is not None:
                    _register_candidate(v)
                if i % batch_step == 0 or i == len(all_posts):
                    _emit_progress(i)
                if len(pending_uniques) >= db_flush_pending or i % db_flush_step == 0:
                    _flush_pending()
                if i % 500 == 0:
                    _LOG.info(
                        "Обработано постов: %s/%s; прошло вакансий: %s",
                        i,
                        len(all_posts),
                        len(candidates),
                    )
        else:
            label = "regex+AI" if ai_service else "regex"
            _LOG.info(
                "Пост-обработка %s постов в %s потоков (%s).",
                len(all_posts),
                workers,
                label,
            )
            for i, v in _process_posts_parallel(
                all_posts,
                workers=workers,
                pool_cls=pool_cls,
                process_one=_process_one,
                cancel=cancel,
                batch_step=batch_step,
                emit_progress=_emit_progress,
            ):
                if v is not None:
                    _register_candidate(v)
                if len(pending_uniques) >= db_flush_pending or i % db_flush_step == 0:
                    _flush_pending()
                if i % 500 == 0:
                    _LOG.info(
                        "Обработано постов: %s/%s; прошло вакансий: %s",
                        i,
                        len(all_posts),
                        len(candidates),
                    )

        process_elapsed = time.monotonic() - process_started
        process_span.set_attribute("parse.vacancies_found", len(candidates))
        _end_span(process_span, process_ctx_token)
        process_span = process_ctx_token = None
        _emit_progress(len(all_posts), phase="post_processing_done")
        if ai_service is not None:
            ai_service.log_batch_finished()
            if not _api_run(cli_overrides):
                all_warnings.append(ai_service.stats.format_token_summary())
                cost_summary = ai_service.stats.format_cost_summary(
                    config.llm_model_catalog,
                    config.ai.model,
                )
                if cost_summary:
                    all_warnings.append(cost_summary)
            if ai_service._client.rate_limiter.llm_disabled:
                all_warnings.append(
                    f"AI: LLM отключён ({ai_service._client.rate_limiter.llm_disabled_reason}). "
                    "Оставшиеся посты обработаны только rules."
                )
        _LOG.info(
            "Пост-обработка завершена за %.1f с. После фильтров осталось вакансий-кандидатов: %s",
            process_elapsed,
            len(candidates),
        )

        _flush_pending()
        return _build_parse_result(
            config=config,
            cli_overrides=cli_overrides,
            all_warnings=all_warnings,
            posts_fetched=posts_fetched,
            candidates=candidates,
            unique=deduplicator.unique,
            dups=deduplicator.duplicates,
            dedup_elapsed_sec=dedup_elapsed,
            ai_service=ai_service,
            posts_processed=len(all_posts),
            fetch_elapsed_sec=fetch_elapsed,
            process_elapsed_sec=process_elapsed,
        )
    except ParseCancelled:
        _LOG.warning("Прогон прерван (отмена).")
        all_warnings.append("Прогон прерван пользователем.")
        try:
            _flush_pending()
        except Exception as flush_err:
            _LOG.warning("Финальный сброс вакансий при отмене не удался: %s", flush_err)
        return _build_parse_result(
            config=config,
            cli_overrides=cli_overrides,
            all_warnings=all_warnings,
            posts_fetched=posts_fetched,
            candidates=candidates,
            unique=deduplicator.unique,
            dups=deduplicator.duplicates,
            dedup_elapsed_sec=dedup_elapsed,
            ai_service=ai_service,
            posts_processed=len(all_posts),
            fetch_elapsed_sec=fetch_elapsed,
            process_elapsed_sec=process_elapsed,
        )
    except KeyboardInterrupt:
        if cancel is not None:
            cancel.request_cancel()
        _LOG.warning("Прогон прерван (Ctrl+C).")
        raise
    finally:
        if process_span is not None:
            process_span.set_attribute("parse.interrupted", True)
            _end_span(process_span, process_ctx_token)
        if ai_service is not None:
            ai_service.close()
