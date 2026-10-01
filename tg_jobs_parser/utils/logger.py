"""Настройка логирования в консоль и файл.

Запись асинхронная: на root висит только ``QueueHandler`` (кладёт запись в
очередь, не блокируя event loop / поток запроса), реальные handlers (stdout,
``app.log``) работают в фоновом потоке ``QueueListener``. Файл ротируется по
размеру, старые части сжимаются в ``app.log.N.gz``.
"""

from __future__ import annotations

import atexit
import gzip
import logging
import os
import queue
import shutil
import sys
from contextvars import ContextVar
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
"""ID текущего HTTP-запроса (MDC): ставит ``backend.http_logging``, попадает в каждую строку лога."""

LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5

_listener: QueueListener | None = None


class RequestIdFilter(logging.Filter):
    """Добавляет ``record.request_id`` и префикс ``request_id=… | `` (пустой вне запроса)."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_var.get()
        record.request_id = rid
        record.request_id_part = f"request_id={rid} | " if rid else ""
        return True


def _gzip_namer(name: str) -> str:
    return f"{name}.gz"


def _gzip_rotator(source: str, dest: str) -> None:
    with open(source, "rb") as src, gzip.open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.remove(source)


def stop_logging() -> None:
    """Дописать очередь и закрыть handlers (вызывается и через atexit)."""
    global _listener
    if _listener is None:
        return
    _listener.stop()
    for handler in _listener.handlers:
        handler.close()
    _listener = None


atexit.register(stop_logging)


def setup_logging(
    log_dir: str | Path | None = "logs",
    level: int = logging.INFO,
    *,
    max_bytes: int = LOG_FILE_MAX_BYTES,
    backup_count: int = LOG_FILE_BACKUP_COUNT,
) -> None:
    """
    Корневой логгер приложения.

    Если ``log_dir`` задан — пишется ``app.log`` (ротация по ``max_bytes``,
    ``backup_count`` сжатых частей). Иначе только консоль.
    ``LOG_DIR`` из env: пустая строка — только stdout (удобно для Docker).
    """
    global _listener
    env_log_dir = os.environ.get("LOG_DIR")
    if env_log_dir is not None:
        log_dir = env_log_dir.strip() or None

    stop_logging()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(request_id_part)s%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir:
        p = Path(log_dir)
        p.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            p / "app.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.namer = _gzip_namer
        fh.rotator = _gzip_rotator
        handlers.append(fh)
    for handler in handlers:
        handler.setFormatter(formatter)

    log_queue: queue.SimpleQueue = queue.SimpleQueue()
    queue_handler = QueueHandler(log_queue)
    queue_handler.addFilter(RequestIdFilter())
    root.addHandler(queue_handler)
    _listener = QueueListener(log_queue, *handlers, respect_handler_level=True)
    _listener.start()

    for name in ("tg_jobs_parser", "backend"):
        logging.getLogger(name).setLevel(level)

    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
