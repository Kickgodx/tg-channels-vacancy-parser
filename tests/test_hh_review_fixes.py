"""Регрессии по Bugbot: content_hash reset, канон/дубли, mid-group flush."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.db.models  # noqa: F401
from backend.db import hh_repository as hh_repo
from backend.db.base import Base
from backend.db.models import HhListingRow, VacancyRow
from backend.services import hh_sync_service
from backend.settings import Settings
from tg_jobs_parser.config.schema import AppConfig, HhConfig, HhSearchConfig
from tg_jobs_parser.hh.dedup import DedupedVacancy
from tg_jobs_parser.hh.mapper import hh_source_post_key, stable_vacancy_id
from tg_jobs_parser.hh.models import VacancyShort


def _short(vid: str, *, area: str = "Москва", req: str = "Python") -> VacancyShort:
    return VacancyShort.model_validate(
        {
            "id": vid,
            "name": "QA Automation Engineer",
            "alternate_url": f"https://hh.ru/vacancy/{vid}",
            "employer": {"id": "100", "name": "ACME"},
            "area": {"name": area},
            "snippet": {"requirement": req, "responsibility": "автотесты"},
            "work_format": [{"id": "REMOTE"}],
            "experience": {"id": "between1And3"},
        }
    )


def test_content_hash_change_resets_detail_fetched_at():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.now(UTC)

    with SessionLocal() as session:
        session.add(
            HhListingRow(
                hh_id="1",
                name="QA",
                content_hash="oldhash",
                detail_fetched_at=now,
                last_seen_at=now,
            )
        )
        session.commit()

        hh_repo.upsert_listings_from_shorts(session, [_short("1", req="Java")])
        session.commit()
        row = session.get(HhListingRow, "1")
        assert row is not None
        assert row.detail_fetched_at is None
        assert row.content_hash != "oldhash"


def test_employer_area_truncated():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    long_name = "X" * 400
    short = VacancyShort.model_validate(
        {
            "id": "9",
            "name": "Job",
            "employer": {"name": long_name},
            "area": {"name": long_name},
        }
    )
    with SessionLocal() as session:
        hh_repo.upsert_listings_from_shorts(session, [short])
        session.commit()
        row = session.get(HhListingRow, "9")
        assert row is not None
        assert len(row.employer or "") <= 256
        assert len(row.area or "") <= 256


def test_stabilize_prefers_detail_fetched_canonical():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.now(UTC)
    by_id = {
        "1": _short("1", area="Москва"),
        "2": _short("2", area="Казань"),
    }
    groups = [
        DedupedVacancy(
            id="1",
            name="QA",
            employer="ACME",
            area="Москва",
            url="https://hh.ru/vacancy/1",
            duplicates=[("2", "Казань")],
        )
    ]
    with SessionLocal() as session:
        session.add(
            HhListingRow(
                hh_id="2",
                name="QA",
                content_hash="h",
                detail_fetched_at=now,
                last_seen_at=now,
            )
        )
        session.commit()
        stabilized = hh_sync_service._stabilize_groups(session, groups, by_id)
        assert len(stabilized) == 1
        assert stabilized[0].id == "2"
        assert stabilized[0].duplicates == [("1", "Москва")]


def test_duplicate_does_not_overwrite_existing_detail_vacancy():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.now(UTC)
    vid = "42"
    with SessionLocal() as session:
        session.add(
            VacancyRow(
                id=stable_vacancy_id(vid),
                source_post_key=hh_source_post_key(vid),
                source_channel="hh",
                source_url="https://hh.ru",
                post_url=f"https://hh.ru/vacancy/{vid}",
                raw_text="<p>full detail body</p>",
                cleaned_text="full detail body",
                extraction_source="hh_api",
                vacancy_score=100,
                parsed_at=now,
            )
        )
        session.commit()

        buffer: list = []
        result = hh_sync_service.HhSyncResult()
        hh_sync_service._append_duplicate(
            MagicMock(),
            session,
            _short(vid, area="Казань"),
            result,
            buffer,
            group_vacancy_id=stable_vacancy_id("1"),
        )
        assert len(buffer) == 1
        assert "full detail body" in buffer[0].cleaned_text
        assert buffer[0].is_duplicate is True


def test_mid_group_flush_when_duplicates_exceed_batch(monkeypatch):
    """Мультигородной кластер > batch_size — flush внутри группы."""
    shorts = [_short(str(i), area=f"City{i}") for i in range(1, 6)]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def iter_vacancy_pages(self, *a, **k):
            page = MagicMock()
            page.found = len(shorts)
            page.items = shorts
            yield page

        def get_vacancy(self, vacancy_id: str):
            from tg_jobs_parser.hh.models import VacancyDetail

            return VacancyDetail.model_validate(
                {
                    "id": vacancy_id,
                    "name": "QA Automation Engineer",
                    "description": f"<p>desc {vacancy_id}</p>",
                    "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
                    "employer": {"id": "100", "name": "ACME"},
                    "area": {"name": "Москва"},
                    "work_format": [{"id": "REMOTE"}],
                    "snippet": {"requirement": "Python", "responsibility": "автотесты"},
                    "experience": {"id": "between1And3"},
                }
            )

    flush_sizes: list[int] = []

    def fake_upsert(session, vacancies, *, fx_config=None):
        flush_sizes.append(len(vacancies))
        return SimpleNamespace(inserted=len(vacancies), updated_existing=0)

    # Все shorts дедупятся в одну группу (одинаковый content key).
    monkeypatch.setattr(hh_sync_service, "HHClient", FakeClient)
    monkeypatch.setattr(
        hh_sync_service.hh_repo,
        "upsert_listings_from_shorts",
        lambda *a, **k: len(shorts),
    )
    monkeypatch.setattr(hh_sync_service.hh_repo, "listing_detail_fetched", lambda *a, **k: False)
    monkeypatch.setattr(hh_sync_service.hh_repo, "mark_detail_fetched", lambda *a, **k: None)
    monkeypatch.setattr(hh_sync_service.hh_repo, "get_vacancy_by_source_post_key", lambda *a, **k: None)
    monkeypatch.setattr(hh_sync_service.hh_repo, "upsert_hh_vacancies", fake_upsert)
    monkeypatch.setattr(hh_sync_service.hh_repo, "purge_expired_listings", lambda *a, **k: 0)
    monkeypatch.setattr(hh_sync_service.hh_repo, "try_acquire_hh_sync_lock", lambda s: True)
    monkeypatch.setattr(hh_sync_service.hh_repo, "release_hh_sync_lock", lambda s: None)

    session = MagicMock()
    config = AppConfig(
        hh=HhConfig(
            enabled=True,
            db_flush_batch_size=2,
            search=HhSearchConfig(text="Python", professional_role=["124"], per_page=100),
        )
    )
    settings = Settings(HH_API_TOKEN="t", HH_CONTACT_EMAIL="a@b.c")
    result = hh_sync_service.run_hh_sync(session, config=config, settings=settings)

    assert result.unique == 1
    assert result.duplicates == 4
    # 1 detail + 4 short dups = 5 → flush 2+2+1
    assert flush_sizes == [2, 2, 1]
    assert result.flush_batches == 3
