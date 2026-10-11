"""Исполнение parse-джобы (импортируется RQ по dotted-path)."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from backend.db import repository as repo
from backend.db.session import session_scope
from backend.observability import (
    PARSE_RUNS_ACTIVE,
    mark_parse_run_started,
    mark_parse_run_stopped,
    record_parse_run_finished,
)
from backend.services.parse_progress_serialize import snapshot_to_progress_dict
from backend.services.parse_run_events import publish_parse_run_event
from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_cancel import ParseCancellationToken, ParseCancelled
from tg_jobs_parser.parse_progress import ParseProgressSnapshot
from tg_jobs_parser.parse_runner import run_parse

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
        while not stop_cancel_poll.wait(0.5):
            with session_scope() as session:
                if repo.is_parse_run_cancel_requested(session, run_id):
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
                _mark_lease_lost()
                return False
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
        payload = snapshot_to_progress_dict(snap)
        now = time.monotonic()
        if now - _last_db_progress >= 1.0:
            _last_db_progress = now
            with session_scope() as session:
                if not repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner):
                    _mark_lease_lost()
                    return
                repo.patch_run_progress(session, run_id, payload, lease_owner=lease_owner)
            publish_parse_run_event(run_id, event="progress", payload=payload)

    flushed_ids: set[str] = set()
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
        flushed_ids.update(v.id for v in batch)
        _LOG.info(
            "Прогон %s: промежуточная запись — %s вакансий (+%s новых), уже в БД: %s",
            run_id,
            len(batch),
            stats.inserted,
            len(flushed_ids),
        )

    def persist_remaining(unique: list[Vacancy]) -> repo.VacancyUpsertStats:
        nonlocal upsert_totals
        remaining = [v for v in unique if v.id not in flushed_ids]
        with session_scope() as session:
            stats = repo.upsert_vacancies(session, remaining, parse_run_id=run_id)
            repo.archive_expired_vacancies(session)
            upsert_totals = repo.VacancyUpsertStats(
                inserted=upsert_totals.inserted + stats.inserted,
                updated_existing=upsert_totals.updated_existing + stats.updated_existing,
            )
            repo.patch_run_vacancy_upsert_stats(session, run_id, upsert_totals)
        flushed_ids.update(v.id for v in remaining)
        return upsert_totals

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

    try:
        result = run_parse(
            config,
            cli_overrides={"api": True, "run_id": str(run_id)},
            progress_callback=on_progress,
            cancel=cancel_token,
            vacancy_batch_sink=flush_vacancies,
            fx_rates=fx_rates,
        )
        if lease_lost.is_set():
            status = "failed"
            error = "lease lost"
            _LOG.warning("Прогон %s: результат отброшен — lease перехвачен recover", run_id)
        else:
            status = "cancelled" if cancel_token.is_cancelled() else "completed"
            with session_scope() as session:
                wrote = repo.finalize_parse_run(session, run_id, result, status=status, lease_owner=lease_owner)
            if not wrote:
                status = "failed"
                error = "lease lost"
                lease_lost.set()
                _LOG.warning("Прогон %s: finalize no-op (fencing)", run_id)
            else:
                try:
                    remaining_count = len(result.vacancies_unique) - len(flushed_ids)
                    _LOG.info(
                        "Прогон %s: финальное сохранение в PostgreSQL (%s вакансий уже записано по ходу прогона, осталось %s)…",
                        run_id,
                        len(flushed_ids),
                        remaining_count,
                    )
                    totals = persist_remaining(result.vacancies_unique)
                except Exception as vac_err:
                    _LOG.exception("Прогон %s: не удалось сохранить вакансии: %s", run_id, vac_err)
                    with session_scope() as session:
                        row = repo.get_parse_run(session, run_id)
                        if row is not None and row.status == status:
                            row.error_message = f"Вакансии не сохранены в БД: {vac_err}"
                    raise
                _LOG.info(
                    "Прогон %s завершён: %s уникальных в прогоне, в БД +%s новых, %s уже были",
                    run_id,
                    len(result.vacancies_unique),
                    totals.inserted,
                    totals.updated_existing,
                )
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
                        persist_remaining(result.vacancies_unique)
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
            with session_scope() as session:
                if repo.touch_parse_run_lease(session, run_id, lease_owner=lease_owner):
                    repo.patch_run_progress(session, run_id, final_payload, lease_owner=lease_owner)
                    publish_parse_run_event(run_id, event="progress", payload=final_payload)
        if not lease_lost.is_set():
            publish_parse_run_event(run_id, event="done", payload={})
