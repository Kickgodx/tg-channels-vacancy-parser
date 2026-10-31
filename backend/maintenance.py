"""Фоновое обслуживание каталога: эмбеддинги и оценки зарплат, обзор рынка, карта рынка.

Где выполняется — ``MAINTENANCE_BACKEND``:

- ``inprocess`` (по умолчанию: локальный запуск и тесты) — у лидера API, каждая задача в своём
  потоке (``MaintenanceDuties(threaded=True)``);
- ``worker`` — в процессе ``jobdesk-maintenance-worker`` (``backend/workers/maintenance_worker.py``),
  задачи по очереди. t-SNE карты, проходы индексации и LLM-вызов обзора тогда не делят CPU и
  пул соединений с запросами API: у API число мест под запросы равно размеру пула.

Пересчёт карты по кнопке — запрос в ``maintenance_requests``; его подхватывает тот же исполнитель.
Ошибки задач только пишутся в журнал: следующая попытка — по расписанию.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from backend.db import maintenance_requests
from backend.db.session import session_scope
from backend.settings import get_settings
from jobdesk.config.loader import load_config

_LOG = logging.getLogger(__name__)

DIGEST_CHECK_INTERVAL_S = 60 * 60
MAP_CHECK_INTERVAL_S = 10 * 60


def runs_here_inprocess() -> bool:
    """Обслуживание выполняет лидер API (а не отдельный воркер)."""
    return get_settings().maintenance_backend == "inprocess"


def sweep_embeddings() -> None:
    """Проход индексации эмбеддингов и оценок зарплаты по ним."""
    from backend.services.embedding_service import current_model, sweep_vacancies
    from backend.services.salary_estimate_service import sweep_estimates

    try:
        sweep_vacancies()
    except Exception as e:
        _LOG.warning("Индексация эмбеддингов не выполнена: %s", e)
        return
    try:
        cfg = load_config(get_settings().config_path).ai.embeddings
        if cfg.enabled:
            sweep_estimates(model=current_model())
    except Exception as e:
        _LOG.warning("Оценки зарплаты не пересчитаны: %s", e)


def ensure_digest() -> None:
    """Обзор рынка за прошедшую неделю, если его ещё нет."""
    try:
        from backend.services.market_digest_service import ensure_latest_digest

        ensure_latest_digest()
    except Exception as e:
        _LOG.warning("Обзор рынка не сформирован: %s", e)


def run_requested_map_build() -> bool:
    """Пересчитать карту, если её запросил администратор. True — запрос был и снят."""
    from backend.services import market_map_service

    with session_scope() as session:
        requested_at = maintenance_requests.pending_since(session, maintenance_requests.TASK_MARKET_MAP)
    if requested_at is None:
        return False
    try:
        built = market_map_service.build_market_map()
    except market_map_service.MapBusy:
        # Считает другой исполнитель (расписание или прежний запрос) — запрос дождётся его.
        return False
    except Exception as e:
        _LOG.warning("Карта рынка по запросу не пересчитана: %s", e)
    else:
        if built is None:
            _LOG.warning("Карта рынка по запросу не пересчитана: мало вакансий с эмбеддингами")
    # Сбой тоже снимает запрос: иначе он повторялся бы каждый цикл. Администратор нажмёт снова.
    with session_scope() as session:
        maintenance_requests.complete_task(session, maintenance_requests.TASK_MARKET_MAP, requested_at=requested_at)
    return True


def maybe_build_market_map() -> None:
    """Карта, если её нет или она старше ``ai.embeddings.map_interval_hours``."""
    try:
        from backend.services import market_map_service

        cfg = load_config(get_settings().config_path)
        if not cfg.ai.embeddings.enabled:
            return
        with session_scope() as session:
            due = market_map_service.map_is_due(session, cfg.ai.embeddings.map_interval_hours)
        if due:
            market_map_service.build_market_map()
    except Exception as e:
        _LOG.warning("Карта рынка не пересчитана: %s", e)


def _map_rebuild_pending() -> bool:
    try:
        with session_scope() as session:
            return maintenance_requests.pending_since(session, maintenance_requests.TASK_MARKET_MAP) is not None
    except Exception as e:
        _LOG.warning("Запросы пересчёта карты не прочитаны: %s", e)
        return False


def _embedding_interval() -> float | None:
    try:
        return float(load_config(get_settings().config_path).ai.embeddings.sweep_interval_seconds)
    except Exception:
        return None


class MaintenanceDuties:
    """Расписание задач обслуживания; ``tick()`` вызывают лидер API или цикл воркера.

    ``threaded`` — каждая задача в своём потоке (лидер API: tick не ждёт t-SNE и LLM, задача
    не запускается, пока жив её прошлый поток). Иначе задачи идут по очереди в вызывающем потоке.
    """

    def __init__(self, *, threaded: bool, clock: Callable[[], float] = time.monotonic) -> None:
        self._threaded = threaded
        self._clock = clock
        self._last: dict[str, float] = {}
        self._threads: dict[str, threading.Thread] = {}

    def _busy(self, name: str) -> bool:
        thread = self._threads.get(name)
        return thread is not None and thread.is_alive()

    def _due(self, name: str, interval: float) -> bool:
        last = self._last.get(name)
        return last is None or self._clock() - last >= interval

    def _start(self, name: str, fn: Callable[[], object], *, scheduled: bool = True) -> None:
        if scheduled:
            self._last[name] = self._clock()
        if not self._threaded:
            fn()
            return
        thread = threading.Thread(target=fn, name=f"maintenance-{name}", daemon=True)
        self._threads[name] = thread
        thread.start()

    def tick(self) -> None:
        if not self._busy("embeddings"):
            interval = _embedding_interval()
            if interval is not None and self._due("embeddings", interval):
                self._start("embeddings", sweep_embeddings)
        if not self._busy("digest") and self._due("digest", DIGEST_CHECK_INTERVAL_S):
            self._start("digest", ensure_digest)
        if not self._busy("map"):
            scheduled = self._due("map", MAP_CHECK_INTERVAL_S)
            if scheduled or _map_rebuild_pending():
                # Запрос администратора не сдвигает проверку по расписанию.
                self._start("map", self._map_task(scheduled), scheduled=scheduled)

    @staticmethod
    def _map_task(scheduled: bool) -> Callable[[], None]:
        def run() -> None:
            try:
                run_requested_map_build()
            except Exception as e:
                _LOG.warning("Запрос пересчёта карты не выполнен: %s", e)
            if scheduled:
                maybe_build_market_map()

        return run


def kick_requested_map_build() -> None:
    """Режим inprocess: выполнить запрос пересчёта сразу, в фоне, не дожидаясь тика лидера."""

    def run() -> None:
        try:
            run_requested_map_build()
        except Exception as e:
            _LOG.warning("Запрос пересчёта карты не выполнен: %s", e)

    threading.Thread(target=run, name="maintenance-map-request", daemon=True).start()
