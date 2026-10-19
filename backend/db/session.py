from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy
from sqlalchemy.orm import Session, sessionmaker

from backend.settings import get_settings

_engine = None
_SessionLocal: sessionmaker[Session] | None = None

# (pool_size, max_overflow) по роли процесса. API: 20 + 20 = 40 — столько же, сколько потоков
# у threadpool sync-эндпоинтов (anyio). Воркер выполняет одну джобу за раз; parse-worker —
# с LLM-потоками прогона, которые ходят в AI-кэш в БД. API + parse + hh + 4 llm-worker
# укладываются в ~76 соединений при max_connections=100.
POOL_BY_ROLE: dict[str, tuple[int, int]] = {
    "api": (20, 20),
    "parse-worker": (4, 12),
    "llm-worker": (2, 2),
    "hh-worker": (2, 2),
}
_pool_role = "api"


def set_pool_role(role: str) -> None:
    """Роль процесса для размера пула; вызывать в entrypoint до первого запроса к БД."""
    global _pool_role
    _pool_role = role if role in POOL_BY_ROLE else "api"
    if _engine is not None:
        reset_engine()


def pool_limits(settings) -> tuple[int, int]:
    """(pool_size, max_overflow) процесса: ``DB_POOL_SIZE`` / ``DB_MAX_OVERFLOW`` перекрывают умолчание роли.

    У API лимит — на все процессы (``API_WORKERS``) вместе: каждый получает долю, не
    меньше одного постоянного соединения и одного дополнительного, если overflow не 0.
    """
    size, overflow = POOL_BY_ROLE[_pool_role]
    if settings.db_pool_size is not None:
        size = settings.db_pool_size
    if settings.db_max_overflow is not None:
        overflow = settings.db_max_overflow
    workers = getattr(settings, "api_workers", 1) or 1
    if _pool_role == "api" and workers > 1:
        size = max(1, size // workers)
        overflow = max(1, overflow // workers) if overflow > 0 else 0
    return size, overflow


def api_thread_limit(settings) -> int:
    """Потоков threadpool у процесса API — по его пулу: sync-обработчик без соединения не работает."""
    size, overflow = pool_limits(settings)
    return size + overflow


def _json_serializer(obj: object) -> str:
    """UTF-8 JSON без \\uXXXX — читаемее в БД и компактнее для кириллицы."""
    return json.dumps(obj, ensure_ascii=False)


def reset_engine() -> None:
    """Сброс пула (тесты / смена DATABASE_URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    # Кэши чтения корпуса относятся к прежней БД.
    from backend.db.vacancy_repository import invalidate_vacancy_count_cache

    invalidate_vacancy_count_cache()


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        pool_size, max_overflow = pool_limits(settings)
        # Через модуль, а не `from sqlalchemy import create_engine`: OTel-инструментация
        # подменяет `sqlalchemy.create_engine` уже после импорта этого модуля.
        _engine = sqlalchemy.create_engine(
            settings.database_url,
            json_serializer=_json_serializer,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def init_db() -> None:
    from backend.db.migrate import init_schema

    init_schema()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
