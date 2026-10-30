"""Переразбор LLM вакансий без специализации (LLM-джоба ``reextract``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

import pytest
from tests.llm_job_helpers import await_llm_job

from backend.services import reextract_service
from jobdesk.models.vacancy import Vacancy


def _register(client, email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _vacancy(vid: str, *, specs: list[str], channel: str = "chan", archived: bool = False, text: str = "") -> Vacancy:
    body = text or f"Go developer {vid}"
    return Vacancy(
        id=vid,
        source_post_key=f"{channel}-{vid}",
        source_channel=channel,
        source_url=f"https://t.me/s/{channel}",
        post_date=datetime(2026, 10, 1, tzinfo=UTC),
        raw_text=body,
        cleaned_text=body,
        detected_specializations=specs,
        technologies=["Go"],
        extraction_source="ai",
        archived_at=datetime(2026, 10, 2, tzinfo=UTC) if archived else None,
    )


def _seed(vacancies: list[Vacancy]) -> None:
    from backend.db.session import session_scope
    from backend.db.vacancy_repository import upsert_vacancies

    with session_scope() as session:
        upsert_vacancies(session, vacancies, parse_run_id=None)
        for v in vacancies:
            if v.archived_at is not None:
                from backend.db.models import VacancyRow

                session.get(VacancyRow, v.id).archived_at = v.archived_at


def _row(vid: str):
    from backend.db.models import VacancyRow
    from backend.db.session import session_scope

    with session_scope() as session:
        row = session.get(VacancyRow, vid)
        session.expunge(row)
        return row


class _FakeAI:
    """LLM без сети: «пикник» — не вакансия, иначе роль Backend."""

    calls: ClassVar[list[str]] = []

    def __init__(self, config):
        from jobdesk.ai.enricher import RunAIStats

        self.stats = RunAIStats()

    def begin_batch(self, total):
        pass

    def enrich(self, vacancy, *, cancel=None):
        self.stats.calls += 1
        _FakeAI.calls.append(vacancy.id)
        if "пикник" in vacancy.cleaned_text:
            self.stats.dropped_not_job += 1
            return None
        return vacancy.model_copy(update={"detected_specializations": ["Backend"], "extraction_source": "ai"})

    def log_batch_finished(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_ai(monkeypatch, default_test_config) -> list[str]:
    from backend.api.routes import vacancies as vacancies_route
    from jobdesk.config.loader import load_config

    real_cfg = load_config(default_test_config)
    ai_cfg = real_cfg.model_copy(update={"ai": real_cfg.ai.model_copy(update={"enabled": True})})
    monkeypatch.setattr(reextract_service, "load_config", lambda *a, **k: ai_cfg)
    monkeypatch.setattr(vacancies_route, "load_config", lambda *a, **k: ai_cfg)
    monkeypatch.setattr("jobdesk.ai.enricher.AIEnrichmentService", _FakeAI, raising=True)
    monkeypatch.setattr(_FakeAI, "calls", [])
    return _FakeAI.calls


def test_reextract_fixes_only_active_telegram_vacancies_without_role(api_client, fake_ai):
    token = _register(api_client, "admin@example.com")
    _seed(
        [
            _vacancy("go", specs=[]),
            _vacancy("picnic", specs=[], text="собираемся на пикник"),
            _vacancy("qa", specs=["QA"]),
            _vacancy("hh", specs=[], channel="hh"),
            _vacancy("old", specs=[], archived=True),
        ]
    )

    info = api_client.get("/api/v1/vacancies/reextract", headers=_auth(token)).json()
    assert info["missing_role"] == 2 and info["ai_enabled"] is True and info["active_job_id"] is None

    job = await_llm_job(api_client, token, api_client.post("/api/v1/vacancies/reextract", headers=_auth(token)))

    assert job["status"] == "done", job
    assert job["kind"] == "reextract"
    result = job["reextract_result"]
    assert (result["total"], result["updated"], result["kept"], result["errors"]) == (2, 1, 1, 0)
    assert sorted(fake_ai) == ["go", "picnic"]
    assert _row("go").detected_specializations == ["Backend"]
    assert _row("go").technologies == ["Go"]
    # «Не вакансия» строку не удаляет и не меняет; HH и архив не трогаются.
    assert _row("picnic").detected_specializations == []
    assert _row("hh").detected_specializations == []
    assert _row("old").detected_specializations == []


def test_reextract_is_admin_only(api_client, fake_ai):
    _register(api_client, "owner@example.com")  # первый — admin
    guest = _register(api_client, "guest@example.com")
    assert api_client.get("/api/v1/vacancies/reextract", headers=_auth(guest)).status_code == 403
    assert api_client.post("/api/v1/vacancies/reextract", headers=_auth(guest)).status_code == 403


def test_reextract_without_targets_or_ai_is_409(api_client, fake_ai, monkeypatch):
    from backend.api.routes import vacancies as vacancies_route

    token = _register(api_client, "admin@example.com")
    r = api_client.post("/api/v1/vacancies/reextract", headers=_auth(token))
    assert r.status_code == 409
    assert "нечего" in r.json()["detail"]["message"]

    _seed([_vacancy("go", specs=[])])
    cfg = vacancies_route.load_config()
    off = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"enabled": False})})
    monkeypatch.setattr(vacancies_route, "load_config", lambda *a, **k: off)
    r = api_client.post("/api/v1/vacancies/reextract", headers=_auth(token))
    assert r.status_code == 409
    assert "AI выключен" in r.json()["detail"]["message"]


def test_reextract_reuses_active_job(api_client, fake_ai):
    from backend.db import llm_job_repository as lj
    from backend.db.session import session_scope

    token = _register(api_client, "admin@example.com")
    _seed([_vacancy("go", specs=[])])
    with session_scope() as session:
        from backend.db.models import UserRow

        admin = session.query(UserRow).filter_by(email="admin@example.com").one()
        row = lj.create_llm_job(session, user_id=admin.id, kind="reextract", params={"vacancy_ids": ["go"]})
        active_id = row.id

    r = api_client.post("/api/v1/vacancies/reextract", headers=_auth(token))
    assert r.status_code == 202
    assert r.json()["job_id"] == str(active_id)
    info = api_client.get("/api/v1/vacancies/reextract", headers=_auth(token)).json()
    assert info["active_job_id"] == str(active_id)


def _running_job(ids: list[str], *, result: dict | None = None) -> tuple:
    from backend.db import llm_job_repository as lj
    from backend.db.models import UserRow
    from backend.db.session import session_scope

    with session_scope() as session:
        user = UserRow(email="w@example.com", password_hash="x", is_admin=True)
        session.add(user)
        session.flush()
        row = lj.create_llm_job(session, user_id=user.id, kind="reextract", params=reextract_service.job_params(ids))
        row.result = result
        job_id, user_id = row.id, user.id
    with session_scope() as session:
        assert lj.claim_llm_job(session, job_id, lease_owner="owner-1") is not None
    return job_id, user_id


def _job_result(job_id):
    from backend.db import llm_job_repository as lj
    from backend.db.session import session_scope

    with session_scope() as session:
        return lj.get_llm_job(session, job_id).result


def test_reextract_retry_skips_processed_and_reports_deleted(api_client, fake_ai):
    _seed([_vacancy("a", specs=[]), _vacancy("b", specs=[])])
    partial = {"total": 3, "warning": None, "items": [{"index": 0, "vacancy_id": "a", "status": "updated"}]}
    job_id, user_id = _running_job(["a", "b", "gone"], result=partial)

    ok = reextract_service.run_reextract_job(
        job_id, user_id=user_id, params={"vacancy_ids": ["a", "b", "gone"]}, result=partial, lease_owner="owner-1"
    )

    assert ok is True
    assert fake_ai == ["b"]
    items = _job_result(job_id)["items"]
    assert [(it["vacancy_id"], it["status"]) for it in items] == [("a", "updated"), ("b", "updated"), ("gone", "error")]


def test_reextract_lost_lease_writes_nothing(api_client, fake_ai):
    _seed([_vacancy("a", specs=[])])
    job_id, user_id = _running_job(["a"])

    ok = reextract_service.run_reextract_job(
        job_id, user_id=user_id, params={"vacancy_ids": ["a"]}, result=None, lease_owner="someone-else"
    )

    assert ok is False
    assert _job_result(job_id) is None
    assert _row("a").detected_specializations == []


def test_reextract_cancel_stops_between_vacancies(api_client, fake_ai):
    from backend.services.llm_job_service import LlmJobCancelled

    _seed([_vacancy("a", specs=[]), _vacancy("b", specs=[])])
    job_id, user_id = _running_job(["a", "b"])
    checks = iter([False, True])

    with pytest.raises(LlmJobCancelled):
        reextract_service.run_reextract_job(
            job_id,
            user_id=user_id,
            params={"vacancy_ids": ["a", "b"]},
            result=None,
            lease_owner="owner-1",
            is_cancelled=lambda: next(checks),
        )

    assert fake_ai == ["a"]
    assert [it["vacancy_id"] for it in _job_result(job_id)["items"]] == ["a"]


def test_reextract_rq_timeout_grows_with_vacancies():
    from backend.services.llm_job_service import IMPORT_SECONDS_PER_TEXT, rq_job_timeout
    from backend.settings import get_settings

    base = get_settings().llm_rq_job_timeout_seconds
    assert rq_job_timeout("reextract", {"vacancy_ids": ["a"] * 10}) == base + 10 * IMPORT_SECONDS_PER_TEXT
