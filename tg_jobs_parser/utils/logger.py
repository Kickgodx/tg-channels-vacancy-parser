"""Настройка логирования в консоль и файл."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def setup_logging(log_dir: str | Path | None = "logs", level: int = logging.INFO) -> None:
    """
    Корневой логгер приложения.

    Если ``log_dir`` задан — пишется ``app.log``. Иначе только консоль.
    ``LOG_DIR`` из env: пустая строка — только stdout (удобно для Docker).
    """
    env_log_dir = os.environ.get("LOG_DIR")
    if env_log_dir is not None:
        log_dir = env_log_dir.strip() or None

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    if log_dir:
        p = Path(log_dir)
        p.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(p / "app.log", encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    for name in ("tg_jobs_parser", "backend"):
        logging.getLogger(name).setLevel(level)

    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
