"""Запуск parse_runner в фоновом потоке + запись в PostgreSQL."""

from __future__ import annotations

import logging
import threading
import uuid

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_cancel import ParseCancelled
from tg_jobs_parser.parse_progress import ParseProgressSnapshot
from tg_jobs_parser.parse_runner import run_parse

from backend.config_builder import config_snapshot_dict
from backend.db import repository as repo
from backend.db.session import session_scope
from backend.schemas import RunCreateRequest
from backend.services.progress_bus import progress_bus
from backend.services.run_registry import run_registry

_LOG = logging.getLogger(__name__)


def start_parse_run(config: AppConfig, body: RunCreateRequest) -> uuid.UUID:
    if run_registry.has_active():
        raise RuntimeError("Уже выполняется прогон парсинга. Дождитесь завершения или отмените его.")

    snapshot = config_snapshot_dict(config)
    with session_scope() as session:
        row = repo.create_parse_run(session, config_snapshot=snapshot)
        run_id = row.id

    active = run_registry.register(run_id)
    with session_scope() as session:
        repo.set_run_status(session, run_id, "running")

    thread = threading.Thread(
        target=_run_worker,
        args=(run_id, config, active.cancel_token),
        name=f"parse-run-{run_id}",
        daemon=True,
    )
    thread.start()
    return run_id


def cancel_run(run_id: uuid.UUID) -> bool:
    active = run_registry.get(run_id)
    if active is None or active.status != "running":
        return False
    active.cancel_token.request_cancel()
    return True


def _run_worker(run_id: uuid.UUID, config: AppConfig, cancel_token) -> None:
    from tg_jobs_parser.utils.logger import setup_logging

    setup_logging()

    active = run_registry.get(run_id)
    if active is None:
        return

    _LOG.info(
        "Прогон %s: старт worker, AI=%s, кэш=%s (%s).",
        run_id,
        config.ai.enabled,
        config.ai.use_cache if config.ai.enabled else "—",
        config.ai.cache_backend if config.ai.enabled else "—",
    )

    def on_progress(snap: ParseProgressSnapshot) -> None:
        active.last_progress = snap
        progress_bus.publish(run_id, snap)

    # Промежуточные батчи, уже записанные в БД по ходу прогона (через vacancy_batch_sink).
    flushed_ids: set[str] = set()
    upsert_totals = repo.VacancyUpsertStats()

    def flush_vacancies(batch: list[Vacancy]) -> None:
        """Промежуточная запись батча уникальных вакансий во время обработки."""
        nonlocal upsert_totals
        with session_scope() as session:
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
        """Финальный UPSERT хвоста (в т.ч. батчей, не записавшихся по ходу) + итоговая статистика."""
        nonlocal upsert_totals
        remaining = [v for v in unique if v.id not in flushed_ids]
        with session_scope() as session:
            stats = repo.upsert_vacancies(session, remaining, parse_run_id=run_id)
            repo.delete_expired_vacancies(session)
            upsert_totals = repo.VacancyUpsertStats(
                inserted=upsert_totals.inserted + stats.inserted,
                updated_existing=upsert_totals.updated_existing + stats.updated_existing,
            )
            repo.patch_run_vacancy_upsert_stats(session, run_id, upsert_totals)
        flushed_ids.update(v.id for v in remaining)
        return upsert_totals

    result: ParseResult | None = None
    try:
        result = run_parse(
            config,
            cli_overrides={"api": True, "run_id": str(run_id)},
            progress_callback=on_progress,
            cancel=cancel_token,
            vacancy_batch_sink=flush_vacancies,
        )
        active.result = result
        final_status = "cancelled" if cancel_token.is_cancelled() else "completed"
        active.status = final_status
        with session_scope() as session:
            repo.finalize_parse_run(session, run_id, result, status=final_status)
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
                if row is not None:
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
        active.status = "cancelled"
        partial = result or active.result
        if partial is not None:
            with session_scope() as session:
                repo.finalize_parse_run(session, run_id, partial, status="cancelled")
            try:
                persist_remaining(partial.vacancies_unique)
            except Exception as vac_err:
                _LOG.warning("Прогон %s отменён; вакансии не сохранены: %s", run_id, vac_err)
        else:
            with session_scope() as session:
                repo.set_run_status(session, run_id, "cancelled")
        _LOG.warning("Прогон %s отменён", run_id)
    except Exception as e:
        active.status = "failed"
        active.error = str(e)
        _LOG.exception("Прогон %s упал: %s", run_id, e)
        with session_scope() as session:
            if result is not None:
                repo.finalize_parse_run(session, run_id, result, status="failed")
                row = repo.get_parse_run(session, run_id)
                if row is not None:
                    row.error_message = str(e)
            else:
                repo.set_run_status(session, run_id, "failed", error_message=str(e))
    finally:
        progress_bus.publish_done(run_id)
        run_registry.finish(run_id)
