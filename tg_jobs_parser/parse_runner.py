"""Оркестрация: Telethon → извлечение → фильтры → дедуп → метрики."""

from __future__ import annotations

import contextvars
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from opentelemetry import context as otel_context
from opentelemetry import trace

from tg_jobs_parser.analytics.metrics_builder import build_metrics
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.deduplication.deduplicator import VacancyDeduplicator
from tg_jobs_parser.fx.vacancy_rub import enrich_vacancies_salary_rub
from tg_jobs_parser.ingestion.policy import IngestPolicy
from tg_jobs_parser.ingestion.process import process_raw_post
from tg_jobs_parser.ingestion.rules import (
    raw_post_to_vacancy,
    raw_post_to_vacancy_rules,
    rules_from_raw_post,
)
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
from tg_jobs_parser.utils.logger import get_logger
from tg_jobs_parser.utils.sorting import sort_vacancies_for_report
from tg_jobs_parser.utils.thread_pool import DaemonThreadPoolExecutor

# Обратная совместимость: тесты/импорты из parse_runner.
__all__ = [
    "raw_post_to_vacancy",
    "raw_post_to_vacancy_rules",
    "rules_from_raw_post",
    "run_parse",
]

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
    from tg_jobs_parser.parsers.telegram_telethon import (
        TelegramTelethonParser,
        telethon_import_error,
    )

    if err := telethon_import_error():
        raise RuntimeError(
            "Нужен Telethon: poetry install (и при необходимости --extras telethon). "
            "Сессия: poetry run python -m tg_jobs_parser.cli login"
        ) from err
    return TelegramTelethonParser(config, cancellation=cancellation)


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
    fx_rates: dict[date, dict[str, float]] | None = None,
    incomplete_reason: str | None = None,
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
            rates_for_date=fx_rates,
        )
        _LOG.info(
            "Курсы для метрик прогона: %s вакансий за %.1f с%s.",
            len(unique),
            time.monotonic() - enrich_started,
            " (из БД)" if fx_rates else "",
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
        incomplete_reason=incomplete_reason,
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
        futures = {ex.submit(contextvars.copy_context().run, process_one, post): post for post in all_posts}
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
    fx_rates: dict[date, dict[str, float]] | None = None,
) -> ParseResult:
    """
    Основной сценарий MVP.

    ``cli_overrides`` — простой dict для сериализации в ``run_params`` (опционально).
    ``vacancy_batch_sink`` — опциональный колбэк: вызывается с очередной порцией
    уникальных вакансий каждые ``parser.db_flush_every_posts`` обработанных постов,
    при накоплении ``parser.db_flush_pending_vacancies`` незаписанных уникальных
    (что наступит раньше) и в конце прогона — чтобы сохранять вакансии в БД по ходу
    обработки, а не только в конце.
    ``fx_rates`` — готовые курсы (из БД); если заданы, метрики не ходят в ЦБ.
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
            "Telegram: Telethon (MTProto). Убедитесь, что активна сессия и заданы TELEGRAM_API_ID / TELEGRAM_API_HASH."
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
    partial_run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
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
            fetch_span.set_attribute("parse.posts_fetched", sum(len(posts) for _, posts, _ in fetch_results))
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
            fx_rates=fx_rates,
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
    incomplete_reason: str | None = None
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

        tg_policy = IngestPolicy(
            apply_post_filters=True,
            score_threshold_without_ai=threshold if ai_service is None else None,
        )

        def _process_one(post: RawPost) -> Vacancy | None:
            return process_raw_post(
                post,
                config,
                policy=tg_policy,
                ai_service=ai_service,
                spec_map=specs_map,
                tech_list=tech_list,
                wf_map=wf_map,
                exclude_keywords=exclude_kw,
                cancel=cancel,
            )

        def _emit_progress(posts_processed: int, *, phase: str = "processing") -> None:
            if not progress_callback and not partial_dir:
                return
            rl = ai_service._client.rate_limiter if ai_service is not None else None
            # В UI/SSE отдаём уникальные (инкрементальный дедуп уже прогнан):
            # candidates могут делить один content-id → React key warning.
            unique_so_far = list(deduplicator.unique)
            snap = ParseProgressSnapshot(
                posts_processed=posts_processed,
                posts_total=len(all_posts),
                vacancies_found=len(unique_so_far),
                vacancies=unique_so_far,
                phase=phase,
                ai_llm_disabled=bool(rl and rl.llm_disabled),
                ai_llm_disabled_reason=rl.llm_disabled_reason if rl else None,
                ai_tokens_in=ai_service.stats.tokens_in if ai_service else 0,
                ai_tokens_out=ai_service.stats.tokens_out if ai_service else 0,
                ai_skipped_score=ai_service.stats.skipped_score if ai_service else 0,
            )
            if partial_dir and unique_so_far:
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
            rate_limiter = ai_service._client.rate_limiter
            skipped = ai_service.stats.rate_limit_skipped
            if skipped and config.ai.mode != "assist":
                # В primary посты без LLM отбрасываются (см. AIEnrichmentService.enrich):
                # прогон неполный, «completed» скрыл бы потерю остатка.
                incomplete_reason = (
                    f"AI: LLM отключён ({rate_limiter.llm_disabled_reason}); в режиме "
                    f"{config.ai.mode} не разобрано постов: {skipped}. Они не кэшируются — "
                    "повторный прогон после сброса лимита обработает их."
                )
                # Прогон через API показывает причину как error_message — не дублируем.
                if not _api_run(cli_overrides):
                    all_warnings.append(incomplete_reason)
            elif rate_limiter.llm_disabled:
                all_warnings.append(
                    f"AI: LLM отключён ({rate_limiter.llm_disabled_reason}). Оставшиеся посты обработаны только rules."
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
            fx_rates=fx_rates,
            incomplete_reason=incomplete_reason,
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
            fx_rates=fx_rates,
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
