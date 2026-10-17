"""Применение миграций Alembic при старте backend."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.db import models  # noqa: F401
from backend.db.session import get_engine
from backend.settings import project_root

_LOG = logging.getLogger(__name__)


def alembic_ini_path() -> Path:
    return project_root() / "alembic.ini"


def _widen_alembic_version_column() -> None:
    """Alembic по умолчанию создаёт version_num VARCHAR(32); длинные revision id ломают upgrade."""
    with get_engine().begin() as conn:
        length = conn.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'alembic_version' AND column_name = 'version_num'"
            )
        ).scalar()
        # Нет таблицы (пустая база) или уже расширена — ALTER не нужен (он берёт ACCESS EXCLUSIVE).
        if length is not None and length < 64:
            conn.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"))


# Ключ pg_advisory_lock миграций (произвольная константа проекта).
MIGRATION_LOCK_KEY = 7_242_031_001


@contextmanager
def migration_lock(engine) -> Iterator[None]:
    """Взаимоисключение ``upgrade head`` между процессами (pg_advisory_lock).

    Второй процесс / реплика API ждёт, пока первый применит миграции, и затем
    получает уже актуальную схему (upgrade — no-op), а не гонку DDL.
    """
    # AUTOCOMMIT: session-level lock не держит открытую транзакцию на время DDL.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})


def upgrade_head() -> None:
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    with migration_lock(get_engine()):
        _widen_alembic_version_column()
        command.upgrade(cfg, "head")


def init_schema() -> None:
    """Применить alembic upgrade head; ошибка миграции останавливает старт."""
    logging.getLogger("alembic").setLevel(logging.WARNING)
    upgrade_head()
    _LOG.info("Схема БД готова.")
