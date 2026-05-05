"""Настройка логирования в консоль и файл."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(log_dir: str | Path | None = "logs", level: int = logging.INFO) -> None:
    """
    Корневой логгер приложения.

    Если ``log_dir`` задан — пишется ``app.log``. Иначе только консоль.
    """
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


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
