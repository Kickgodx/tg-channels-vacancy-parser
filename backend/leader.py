"""Лидер процессов API: общую для всех процессов работу выполняет один из них.

Плановая очистка, синхронизация курсов при старте и сервер ``/metrics`` не должны
повторяться в каждом процессе (``API_WORKERS``) и в каждом контейнере API. Лидер —
процесс, удерживающий ``pg_try_advisory_lock`` на своём соединении. Остальные
пробуют взять lock раз в ``interval_s``: если лидер упал, Postgres снимает lock
вместе с его сессией, и роль переходит к другому процессу.

Lock на отдельном соединении без пула (AUTOCOMMIT): соединение пула вернулось бы
в пул вместе с lock (см. ``HhSyncDbLock``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import sqlalchemy
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from backend.settings import get_settings

_LOG = logging.getLogger(__name__)

# Ключ pg_advisory_lock лидера API (произвольная константа проекта, ≠ миграциям и HH).
API_LEADER_LOCK_KEY = 7_242_031_002
# Как часто не-лидер пробует взять роль, а лидер проверяет соединение и выполняет tick.
LEADER_INTERVAL_S = 30.0


class LeaderLock:
    """Advisory lock лидера на собственном соединении."""

    def __init__(self, key: int = API_LEADER_LOCK_KEY) -> None:
        self._key = key
        self._engine: Engine | None = None
        self._conn: Connection | None = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            # Через модуль: OTel-инструментация подменяет sqlalchemy.create_engine после импорта.
            self._engine = sqlalchemy.create_engine(get_settings().database_url, poolclass=sqlalchemy.NullPool)
        return self._engine

    def try_acquire(self) -> bool:
        if self._conn is not None:
            return True
        conn = self._get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._key}).scalar())
        except Exception:
            conn.close()
            raise
        if not got:
            conn.close()
            return False
        self._conn = conn
        return True

    def alive(self) -> bool:
        """Соединение с lock живо; иначе lock уже снят Postgres — роль потеряна."""
        if self._conn is None:
            return False
        try:
            self._conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            _LOG.warning("Соединение с advisory lock лидера потеряно: %s", exc)
            self._drop()
            return False

    def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self._key})
            except Exception as exc:
                _LOG.warning("Снять lock лидера API не удалось, соединение закрыто: %s", exc)
            finally:
                conn.close()
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.invalidate()
                conn.close()
            except Exception:
                pass


class ApiLeader:
    """Цикл лидерства: ``on_acquire`` при получении роли, ``on_tick`` у лидера, ``on_lose`` при потере.

    Колбэки не должны бросать: исключение записывается в журнал и не останавливает цикл.
    """

    def __init__(
        self,
        *,
        on_acquire: Callable[[], None],
        on_lose: Callable[[], None],
        on_tick: Callable[[], None] | None = None,
        interval_s: float = LEADER_INTERVAL_S,
        lock: LeaderLock | None = None,
    ) -> None:
        self._on_acquire = on_acquire
        self._on_lose = on_lose
        self._on_tick = on_tick
        self._interval_s = interval_s
        self._lock = lock or LeaderLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_leader = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def try_become_leader(self) -> bool:
        """Одна попытка взять роль; при успехе синхронно выполняет ``on_acquire``."""
        if self._is_leader:
            return True
        try:
            got = self._lock.try_acquire()
        except Exception as exc:
            _LOG.warning("Лидер API: lock не получен: %s", exc)
            return False
        if got:
            self._is_leader = True
            _LOG.info("Процесс стал лидером API: очистка, курсы и /metrics выполняются здесь")
            self._call(self._on_acquire, "on_acquire")
        return got

    def start(self) -> None:
        """Фоновый цикл: попытки взять роль, у лидера — проверка соединения и tick."""
        self._thread = threading.Thread(target=self._run, name="api-leader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._is_leader:
            self._is_leader = False
            self._call(self._on_lose, "on_lose")
        self._lock.release()

    def _run(self) -> None:
        while not self._stop.wait(timeout=self._interval_s):
            if not self._is_leader:
                self.try_become_leader()
            elif not self._lock.alive():
                self._is_leader = False
                _LOG.warning("Процесс потерял роль лидера API")
                self._call(self._on_lose, "on_lose")
            elif self._on_tick is not None:
                self._call(self._on_tick, "on_tick")

    @staticmethod
    def _call(fn: Callable[[], None], name: str) -> None:
        try:
            fn()
        except Exception:
            _LOG.exception("Лидер API: %s упал", name)
