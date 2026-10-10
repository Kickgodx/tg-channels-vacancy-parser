"""Промежуточный flush HH вакансий батчами."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.services import hh_sync_service
from backend.settings import Settings
from tg_jobs_parser.config.schema import AppConfig, HhConfig, HhSearchConfig
from tg_jobs_parser.hh.models import VacancyShort


def test_hh_sync_flushes_in_batches(monkeypatch):
    shorts = [
        VacancyShort.model_validate(
            {
                "id": str(i),
                "name": f"Job {i}",
                "alternate_url": f"https://hh.ru/vacancy/{i}",
                "employer": {"id": str(i), "name": f"Co{i}"},
                "area": {"name": "Москва"},
                "snippet": {"requirement": f"req {i}", "responsibility": "do"},
                "work_format": [{"id": "REMOTE"}],
                "experience": {"id": "between1And3"},
            }
        )
        for i in range(1, 6)
    ]

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
                    "name": f"Job {vacancy_id}",
                    "description": f"<p>desc {vacancy_id}</p>",
                    "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
                    "employer": {"id": vacancy_id, "name": f"Co{vacancy_id}"},
                    "area": {"name": "Москва"},
                    "work_format": [{"id": "REMOTE"}],
                }
            )

    flush_sizes: list[int] = []

    def fake_upsert(session, vacancies, *, fx_config=None):
        flush_sizes.append(len(vacancies))
        return SimpleNamespace(inserted=len(vacancies), updated_existing=0)

    monkeypatch.setattr(hh_sync_service, "HHClient", FakeClient)
    monkeypatch.setattr(
        hh_sync_service.hh_repo,
        "upsert_listings_from_shorts",
        lambda *a, **k: len(shorts),
    )
    monkeypatch.setattr(hh_sync_service.hh_repo, "listing_detail_fetched", lambda *a, **k: False)
    monkeypatch.setattr(hh_sync_service.hh_repo, "mark_detail_fetched", lambda *a, **k: None)
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

    assert result.searched == 5
    assert result.details_fetched == 5
    assert result.flush_batches == 3  # 2+2+1
    assert flush_sizes == [2, 2, 1]
    assert session.commit.call_count >= 3
