"""Применение единственной MVP-миграции Alembic при старте backend."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from backend.db import models  # noqa: F401
from backend.db.base import Base
from backend.db.session import get_engine
from backend.settings import project_root

_LOG = logging.getLogger(__name__)


def alembic_ini_path() -> Path:
    return project_root() / "alembic.ini"


def upgrade_head() -> None:
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def init_schema() -> None:
    """MVP: одна ревизия ``001_initial``. При смене схемы — пересоздать volume Postgres."""
    logging.getLogger("alembic").setLevel(logging.WARNING)
    try:
        upgrade_head()
    except Exception as e:
        _LOG.warning("Alembic upgrade: %s — досоздаём таблицы через metadata.", e)
    Base.metadata.create_all(bind=get_engine())
    _LOG.info("Схема БД готова.")
