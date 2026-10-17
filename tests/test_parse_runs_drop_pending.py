"""Миграция 029: устаревший статус ``pending`` у прогонов закрывается как ``failed``."""

from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.db.migrate import alembic_ini_path
from backend.db.models import ParseRunRow


def test_migration_fails_legacy_pending_runs(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "028_llm_jobs_one_active")

    engine = create_engine(pg_empty_db)
    pending_id, completed_id = uuid.uuid4(), uuid.uuid4()
    with sessionmaker(bind=engine)() as s:
        s.add(ParseRunRow(id=pending_id, status="pending", config_snapshot={}))
        s.add(ParseRunRow(id=completed_id, status="completed", config_snapshot={}))
        s.commit()

    command.upgrade(cfg, "head")
    with sessionmaker(bind=engine)() as s:
        rows = {r.id: r for r in s.scalars(select(ParseRunRow))}
        pending, completed = rows[pending_id], rows[completed_id]
        assert pending.status == "failed"
        assert "pending" in pending.error_message
        assert pending.finished_at is not None
        assert completed.status == "completed"
        assert completed.error_message is None
    engine.dispose()


def test_new_parse_run_defaults_to_queued(db_session):
    row = ParseRunRow(config_snapshot={})
    db_session.add(row)
    db_session.flush()

    assert row.status == "queued"
