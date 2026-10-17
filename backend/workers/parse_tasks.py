"""Исполнение parse-джобы (импортируется RQ по dotted-path)."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from backend.db import repository as repo
from backend.db.channel_checkpoint_repository import (
    format_checkpoint_changes,
    load_channel_checkpoints,
    save_channel_checkpoints,
)
from backend.db.session import session_scope
from backend.observability import (
    PARSE_RUNS_ACTIVE,
    mark_parse_run_started,
    mark_parse_run_stopped,
    record_parse_run_finished,
)
from backend.services.parse_progress_serialize import snapshot_to_progress_dict
from backend.services.parse_run_events import publish_parse_run_event
from backend.workers import liveness
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_cancel import ParseCancellationToken, ParseCancelled
from tg_jobs_parser.parse_progress import ParseProgressSnapshot
from tg_jobs_parser.parse_runner import run_parse
from tg_jobs_parser.parsers.base import ChannelCheckpoint
from tg_jobs_parser.parsers.channel_username import normalize_channel_username

_LOG = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)

__all__ = ["run_parse", "run_parse_job"]


def run_parse_job(run_id: str, traceparent: str | None = None) -> None:
    """Claim queued parse_run и выполнить ``run_parse`` (in-process или RQ worker)."""
    rid = uuid.UUID(str(run_id))
    ctx = None
    if traceparent:
        try:
            from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

            ctx = TraceContextTextMapPropagator().extract(carrier={"traceparent": traceparent})
        except Exception:
            ctx = None

    with _TRACER.start_as_current_span(
        "parse_run",
        context=ctx,
        kind=SpanKind.CONSUMER,
        attributes={"parse.run_id": str(rid)},
    ) as span:
        _run_parse_job_traced(rid, span)


_LEASE_HEARTBEAT_S = 30.0
_LEASE_JOIN_TIMEOUT_S = 5.0


def _run_parse_job_traced(run_id: uuid.UUID, span) -> None:
    from backend.db.parse_run_repository import new_lease_owner

    lease_owner = new_lease_owner("parse")
    with session_scope() as session:
        claimed = repo.claim_parse_run(session, run_id, lease_owner=lease_owner)
        if claimed is None:
            row = repo.get_parse_run(session, run_id)
            if row is not None and row.cancel_requested:
                _LOG.info("Прогон %s: отменён до claim, выход", run_id)
            return
        snapshot = dict(claimed.config_snapshot or {})
        span.set_attribute("parse.channels", len((snapshot.get("sources") or {}).get("channels") or []))
        span.set_attribute("parse.ai_enabled", bool((snapshot.get("ai") or {}).get("enabled")))

    try:
        config = AppConfig.model_validate(snapshot)
    except Exception as e:
        _LOG.exception("Прогон %s: невалидный config_snapshot: %s", run_id, e)
        with session_scope() as session:
            repo.set_run_status(
                session,
                run_id,
                "failed",
                error_message=f"Invalid config_snapshot: {e}",
                lease_owner=lease_owner,
            )
        publish_parse_run_event(run_id, event="done", payload={})
        return

    cancel_token = ParseCancellationToken()
    stop_cancel_poll = threading.Event()
    stop_lease = threading.Event()
    lease_lost = threading.Event()

    def _poll_cancel() -> None:
        failing = False
        while not stop_cancel_poll.wait(0.5):
            try:
                with session_scope() as session:
                    requested = repo.is_parse_run_cancel_requested(session, run_id)
            except Exception as e:
                # Сбой БД не должен завершать поток: иначе отмена этого прогона перестаёт работать.
                if not failing:
                    _LOG.warning("Прогон %s: опрос отмены не удался, повторяем: %s", run_id, e)
                    failing = True
                continue
            failing = False
            if requested:
                cancel_token.request_cancel()
                return

    def _mark_lease_lost() -> None:
        if not lease_lost.is_set():
            _LOG.warning("Прогон %s: lease lost (fencing) — прерываем", run_id)
            lease_lost.set()
            cancel_token.request_cancel()

    def _renew_lease() -> bool:
        try:
            with session_scope() as session:
                ok = repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner)
            if not ok:
                # После stop_lease прогон финализируется: «не running» — уже не потеря lease.
                if not stop_lease.is_set():
                    _mark_lease_lost()
                return False
            liveness.beat()
            return True
        except Exception as e:
            _LOG.debug("Parse run %s lease heartbeat: %s", run_id, e)
            return True  # транзиентная ошибка — не сдаём lease

    def _lease_loop() -> None:
        # Сразу после claim, затем каждые _LEASE_HEARTBEAT_S.
        while True:
            if not _renew_lease():
                return
            if stop_lease.wait(timeout=_LEASE_HEARTBEAT_S):
                return

    poller = threading.Thread(
        target=_poll_cancel,
        name=f"parse-cancel-poll-{run_id}",
        daemon=True,
    )
    poller.start()
    lease_thread = threading.Thread(
        target=_lease_loop,
        name=f"parse-lease-{run_id}",
        daemon=True,
    )
    lease_thread.start()

    PARSE_RUNS_ACTIVE.inc()
    mark_parse_run_started()
    started = time.monotonic()
    status = "running"
    error: str | None = None
    last_progress: ParseProgressSnapshot | None = None
    result: ParseResult | None = None

    _LOG.info(
        "Прогон %s: старт worker, AI=%s, кэш=%s (%s).",
        run_id,
        config.ai.enabled,
        config.ai.use_cache if config.ai.enabled else "—",
        config.ai.cache_backend if config.ai.enabled else "—",
    )

    _last_db_progress = 0.0

    def on_progress(snap: ParseProgressSnapshot) -> None:
        nonlocal _last_db_progress, last_progress
        if lease_lost.is_set():
            cancel_token.request_cancel()
            return
        last_progress = snap
        now = time.monotonic()
        if now - _last_db_progress >= 1.0:
            _last_db_progress = now
            payload = snapshot_to_progress_dict(snap)
            try:
                with session_scope() as session:
                    if not repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner):
                        _mark_lease_lost()
                        return
                    repo.patch_run_progress(session, run_id, payload, lease_owner=lease_owner)
            except Exception as e:
                # Прогресс необязателен: транзиентный сбой БД не должен ронять прогон.
                _LOG.warning("Прогон %s: прогресс не записан: %s", run_id, e)
                return
            publish_parse_run_event(run_id, event="progress", payload=payload)

    # Ключи постов (source_post_key), уже записанных в БД: вакансии и появления дублей.
    flushed_keys: set[str] = set()
    flushed_vacancies = 0

    def unflushed(res: ParseResult) -> list[Vacancy]:
        # Уникальные первыми: каноническая вакансия дубля пишется до его появления.
        return [v for v in (*res.vacancies_unique, *res.duplicates) if v.source_post_key not in flushed_keys]

    def mark_flushed(batch: list[Vacancy]) -> None:
        nonlocal flushed_vacancies
        flushed_keys.update(v.source_post_key for v in batch)
        flushed_vacancies += sum(1 for v in batch if not v.is_duplicate)

    upsert_totals = repo.VacancyUpsertStats()

    def flush_vacancies(batch: list[Vacancy]) -> None:
        nonlocal upsert_totals
        if lease_lost.is_set():
            cancel_token.request_cancel()
            return
        with session_scope() as session:
            if not repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner):
                _mark_lease_lost()
                return
            stats = repo.upsert_vacancies(session, batch, parse_run_id=run_id)
        upsert_totals = repo.VacancyUpsertStats(
            inserted=upsert_totals.inserted + stats.inserted,
            updated_existing=upsert_totals.updated_existing + stats.updated_existing,
        )
        mark_flushed(batch)
        _LOG.info(
            "Прогон %s: промежуточная запись — %s постов (+%s новых вакансий), уже в БД: %s вакансий",
            run_id,
            len(batch),
            stats.inserted,
            flushed_vacancies,
        )

    def save_checkpoints(checkpoints: dict[str, ChannelCheckpoint]) -> None:
        """Чекпоинт канала, чьи вакансии уже записаны, — сразу, а не в финализации.

        Ретрай после падения воркера тогда не перечитывает готовые каналы. Run_parse
        зовёт это только пока ни один пост не потерян; при completed финализация
        перезапишет те же значения.
        """
        if lease_lost.is_set():
            cancel_token.request_cancel()
            return
        with session_scope() as session:
            if not repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner):
                _mark_lease_lost()
                return
            changes = save_channel_checkpoints(session, checkpoints, parse_run_id=run_id)
        if changes:
            _LOG.info("Прогон %s: чекпоинт сдвинут — %s", run_id, format_checkpoint_changes(changes))
        else:
            _LOG.info("Прогон %s: чекпоинт без изменений — %s", run_id, ", ".join(f"@{c}" for c in checkpoints))

    def persist_remaining(res: ParseResult) -> repo.VacancyUpsertStats:
        nonlocal upsert_totals
        remaining = unflushed(res)
        with session_scope() as session:
            stats = repo.upsert_vacancies(session, remaining, parse_run_id=run_id)
            repo.archive_expired_vacancies(session)
            upsert_totals = repo.VacancyUpsertStats(
                inserted=upsert_totals.inserted + stats.inserted,
                updated_existing=upsert_totals.updated_existing + stats.updated_existing,
            )
            repo.patch_run_vacancy_upsert_stats(session, run_id, upsert_totals)
        mark_flushed(remaining)
        return upsert_totals

    def finalize_with_vacancies(res: ParseResult, final_status: str) -> repo.VacancyUpsertStats | None:
        """Статус прогона и остаток вакансий — одной транзакцией. None — lease потерян.

        Сбой записи вакансий откатывает и статус: прогон остаётся ``running`` и
        уходит в ``failed`` общим обработчиком, а не в ``completed`` без данных.
        """
        nonlocal upsert_totals
        remaining = unflushed(res)
        checkpoint_changes = {}
        with session_scope() as session:
            # Fenced UPDATE первым: проверяет lease до записи вакансий.
            if not repo.finalize_parse_run(
                session,
                run_id,
                res,
                status=final_status,
                lease_owner=lease_owner,
                error_message=res.incomplete_reason if final_status == "partial" else None,
            ):
                return None
            stats = repo.upsert_vacancies(session, remaining, parse_run_id=run_id)
            repo.archive_expired_vacancies(session)
            totals = repo.VacancyUpsertStats(
                inserted=upsert_totals.inserted + stats.inserted,
                updated_existing=upsert_totals.updated_existing + stats.updated_existing,
            )
            repo.patch_run_vacancy_upsert_stats(session, run_id, totals)
            if final_status == "completed":
                # Чекпоинты двигаем только вместе с успешной финализацией: иначе
                # следующий прогон пропустил бы посты, которые этот не сохранил.
                checkpoint_changes = save_channel_checkpoints(session, res.channel_checkpoints, parse_run_id=run_id)
        if checkpoint_changes:
            # Обычно пусто: каналы уже сдвинуты по ходу прогона, финализация пишет те же значения.
            _LOG.info(
                "Прогон %s: чекпоинт сдвинут при финализации — %s",
                run_id,
                format_checkpoint_changes(checkpoint_changes),
            )
        upsert_totals = totals
        mark_flushed(remaining)
        return totals

    fx_rates = None
    if config.fx.enabled:
        try:
            from backend.db import fx_repository as fx_repo

            with session_scope() as session:
                rate_date, rates = fx_repo.ensure_latest_rates(session, config.fx)
            if rate_date is not None and rates:
                fx_rates = {rate_date: rates}
                _LOG.info(
                    "Прогон %s: курсы для метрик из БД (as_of=%s, %s валют)",
                    run_id,
                    rate_date.isoformat(),
                    len(rates),
                )
        except Exception as e:
            _LOG.warning("Прогон %s: не удалось взять курсы из БД: %s", run_id, e)

    checkpoints = None
    if config.parser.incremental_fetch:
        try:
            channels = [u for u in (normalize_channel_username(c) for c in config.sources.channels) if u]
            with session_scope() as session:
                checkpoints = load_channel_checkpoints(session, channels)
        except Exception as e:
            _LOG.warning("Прогон %s: чекпоинты каналов не прочитаны, читаем окно целиком: %s", run_id, e)

    try:
        result = run_parse(
            config,
            cli_overrides={"api": True, "run_id": str(run_id)},
            progress_callback=on_progress,
            cancel=cancel_token,
            vacancy_batch_sink=flush_vacancies,
            fx_rates=fx_rates,
            channel_checkpoints=checkpoints,
            checkpoint_sink=save_checkpoints,
        )
        if lease_lost.is_set():
            status = "failed"
            error = "lease lost"
            _LOG.warning("Прогон %s: результат отброшен — lease перехвачен recover", run_id)
        else:
            if cancel_token.is_cancelled():
                final_status = "cancelled"
            elif result.incomplete_reason:
                # Часть постов не разобрана (LLM отключился или много ошибок в primary):
                # собранное сохраняем, но «completed» скрыл бы потерю остатка.
                final_status = "partial"
            else:
                final_status = "completed"
            # Heartbeat останавливаем до финализации: после неё прогон уже не running.
            stop_lease.set()
            lease_thread.join(timeout=_LEASE_JOIN_TIMEOUT_S)
            _LOG.info(
                "Прогон %s: финальное сохранение в PostgreSQL (%s вакансий уже записано по ходу прогона, осталось %s)…",
                run_id,
                flushed_vacancies,
                len(result.vacancies_unique) - flushed_vacancies,
            )
            try:
                totals = finalize_with_vacancies(result, final_status)
            except Exception as vac_err:
                raise RuntimeError(f"Вакансии не сохранены в БД: {vac_err}") from vac_err
            if totals is None:
                status = "failed"
                error = "lease lost"
                lease_lost.set()
                _LOG.warning("Прогон %s: finalize no-op (fencing)", run_id)
            else:
                status = final_status
                _LOG.info(
                    "Прогон %s завершён: %s уникальных в прогоне, в БД +%s новых, %s уже были",
                    run_id,
                    len(result.vacancies_unique),
                    totals.inserted,
                    totals.updated_existing,
                )
                if final_status == "partial":
                    error = result.incomplete_reason
                    _LOG.error("Прогон %s неполный, статус partial: %s", run_id, error)
    except ParseCancelled:
        if lease_lost.is_set():
            status = "failed"
            error = "lease lost"
            _LOG.warning("Прогон %s: остановлен из-за потери lease (не пишем cancelled)", run_id)
        else:
            status = "cancelled"
            wrote = False
            if result is not None:
                with session_scope() as session:
                    wrote = repo.finalize_parse_run(
                        session, run_id, result, status="cancelled", lease_owner=lease_owner
                    )
                if wrote:
                    try:
                        persist_remaining(result)
                    except Exception as vac_err:
                        _LOG.warning("Прогон %s отменён; вакансии не сохранены: %s", run_id, vac_err)
            else:
                with session_scope() as session:
                    wrote = repo.set_run_status(session, run_id, "cancelled", lease_owner=lease_owner)
            if not wrote:
                status = "failed"
                error = "lease lost"
                lease_lost.set()
                _LOG.warning("Прогон %s: cancel finalize no-op (fencing)", run_id)
            else:
                _LOG.warning("Прогон %s отменён", run_id)
    except Exception as e:
        status = "failed"
        error = str(e)
        _LOG.exception("Прогон %s упал: %s", run_id, e)
        if not lease_lost.is_set():
            with session_scope() as session:
                if result is not None:
                    wrote = repo.finalize_parse_run(session, run_id, result, status="failed", lease_owner=lease_owner)
                    if wrote:
                        row = repo.get_parse_run(session, run_id)
                        if row is not None:
                            row.error_message = str(e)
                    else:
                        lease_lost.set()
                else:
                    if not repo.set_run_status(
                        session, run_id, "failed", error_message=str(e), lease_owner=lease_owner
                    ):
                        lease_lost.set()
    finally:
        stop_cancel_poll.set()
        stop_lease.set()
        lease_thread.join(timeout=1.0)
        span.set_attribute("parse.status", status or "unknown")
        if result is not None:
            span.set_attribute("parse.posts_fetched", result.posts_fetched)
            span.set_attribute("parse.vacancies_unique", len(result.vacancies_unique))
        if status == "failed":
            span.set_status(Status(StatusCode.ERROR, error or "failed"))
        record_parse_run_finished(
            status=status,
            posts_fetched=result.posts_fetched if result else 0,
            duration_seconds=time.monotonic() - started,
        )
        PARSE_RUNS_ACTIVE.dec()
        mark_parse_run_stopped()
        if last_progress is not None and not lease_lost.is_set():
            final_payload = snapshot_to_progress_dict(last_progress)
            try:
                with session_scope() as session:
                    if repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner):
                        repo.patch_run_progress(session, run_id, final_payload, lease_owner=lease_owner)
                        publish_parse_run_event(run_id, event="progress", payload=final_payload)
            except Exception as e:
                _LOG.warning("Прогон %s: финальный прогресс не записан: %s", run_id, e)
        if not lease_lost.is_set():
            publish_parse_run_event(run_id, event="done", payload={})
