"""TTL purge для hh_listings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.db.models  # noqa: F401
from backend.db import hh_repository as hh_repo
from backend.db.base import Base
from backend.db.models import HhListingRow


def test_purge_expired_listings():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.now(UTC)

    with SessionLocal() as session:
        session.add(
            HhListingRow(
                hh_id="old",
                name="Old",
                content_hash="abc",
                last_seen_at=now - timedelta(days=50),
            )
        )
        session.add(
            HhListingRow(
                hh_id="new",
                name="New",
                content_hash="def",
                last_seen_at=now - timedelta(days=1),
            )
        )
        session.commit()

        deleted = hh_repo.purge_expired_listings(session, now=now, ttl_days=40)
        session.commit()
        assert deleted == 1
        assert session.get(HhListingRow, "old") is None
        assert session.get(HhListingRow, "new") is not None
