"""Запуск parse_runner в фоновом потоке + запись в PostgreSQL."""

from __future__ import annotations

import logging
import threading
import uuid

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.models.parse_result import ParseResult
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

    result: ParseResult | None = None
    try:
        result = run_parse(
            config,
            cli_overrides={"api": True, "run_id": str(run_id)},
            progress_callback=on_progress,
            cancel=cancel_token,
        )
        active.result = result
        active.status = "completed"
        final_status = "cancelled" if cancel_token.is_cancelled() else "completed"
        with session_scope() as session:
            repo.finalize_parse_run(session, run_id, result, status=final_status)
        try:
            with session_scope() as session:
                repo.upsert_vacancies(session, result.vacancies_unique, parse_run_id=run_id)
        except Exception as vac_err:
            _LOG.exception("Прогон %s: не удалось сохранить вакансии: %s", run_id, vac_err)
            with session_scope() as session:
                row = repo.get_parse_run(session, run_id)
                if row is not None:
                    row.error_message = f"Вакансии не сохранены в БД: {vac_err}"
            raise
        _LOG.info("Прогон %s завершён: %s уникальных вакансий", run_id, len(result.vacancies_unique))
    except ParseCancelled:
        active.status = "cancelled"
        partial = result or active.result
        if partial is not None:
            with session_scope() as session:
                repo.finalize_parse_run(session, run_id, partial, status="cancelled")
            try:
                with session_scope() as session:
                    repo.upsert_vacancies(session, partial.vacancies_unique, parse_run_id=run_id)
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
