from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import sqlalchemy
from sqlalchemy.orm import Session, sessionmaker

from backend.db.base import Base
from backend.settings import get_settings

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def reset_engine() -> None:
    """Сброс пула (тесты / смена DATABASE_URL)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            # busy-timeout: параллельные писатели (worker-потоки, тесты) ждут,
            # а не падают мгновенно с "database is locked".
            connect_args["timeout"] = 30
        pool_kw: dict = {"pool_pre_ping": True}
        if url.startswith("postgresql"):
            pool_kw["pool_size"] = 10
            pool_kw["max_overflow"] = 20
        # Через модуль, а не `from sqlalchemy import create_engine`: OTel-инструментация
        # подменяет `sqlalchemy.create_engine` уже после импорта этого модуля.
        _engine = sqlalchemy.create_engine(url, connect_args=connect_args, **pool_kw)
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
