"""Применение миграций Alembic при старте backend."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.db import models  # noqa: F401
from backend.db.base import Base
from backend.db.session import get_engine
from backend.settings import project_root

_LOG = logging.getLogger(__name__)


def alembic_ini_path() -> Path:
    return project_root() / "alembic.ini"


def _widen_alembic_version_column() -> None:
    """Alembic по умолчанию создаёт version_num VARCHAR(32); длинные revision id ломают upgrade."""
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
            )
        ).scalar()
        if not exists:
            return
        conn.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"))


def upgrade_head() -> None:
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    _widen_alembic_version_column()
    command.upgrade(cfg, "head")


def init_schema() -> None:
    """Применить alembic upgrade head.

    При сбое на пустой/тестовой БД — ``create_all`` и повторный raise, чтобы
    Postgres с устаревшей схемой не поднимал API «успешно» без колонок.
    """
    logging.getLogger("alembic").setLevel(logging.WARNING)
    try:
        upgrade_head()
    except Exception as e:
        _LOG.error(
            "Alembic upgrade failed: %s — create_all как fallback "
            "(на уже существующем Postgres новые колонки так не появятся).",
            e,
        )
        Base.metadata.create_all(bind=get_engine())
        raise
    _LOG.info("Схема БД готова.")
