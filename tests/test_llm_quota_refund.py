"""Возврат вызова суточной квоты, если задача оценки или письма завершилась без ответа модели."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from tests.conftest import new_user
from tests.llm_job_helpers import await_llm_job
from tests.test_llm_jobs import _add_vacancy, _auth, _register

from backend.db import llm_job_repository as lj
from backend.db import user_content_repository as uc
from backend.db.llm_usage_repository import llm_calls_today, try_reserve_llm_calls
from backend.db.models import LlmJobRow, UserRow
from backend.db.session import session_scope
from backend.services import llm_job_service, match_service
from backend.services.llm_job_service import LlmJobCancelled
from backend.settings import get_settings


def _today():
    return datetime.now(UTC).date()


@pytest.fixture
def guest(api_client, monkeypatch) -> tuple:
    """Клиент, токен не-админа (квота 5 в сутки), его резюме и вакансия ``v-1``."""
    monkeypatch.setenv("LLM_USER_DAILY_CALLS", "5")
    get_settings.cache_clear()
    _register(api_client, "owner@example.com")  # первый — admin, квоты у него нет
    token = _register(api_client, "guest@example.com")
    _add_vacancy("v-1")
    rid = api_client.post("/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python"}).json()["id"]
    return api_client, token, rid


def _guest_calls() -> int:
    with session_scope() as session:
        user_id = session.scalar(select(UserRow.id).where(UserRow.email == "guest@example.com"))
        return llm_calls_today(session, user_id=user_id, day=_today())


def test_model_failure_refunds_call(guest, monkeypatch):
    client, token, rid = guest

    def failing(**_kwargs):
        raise RuntimeError("OpenRouter API: 503")

    monkeypatch.setattr(match_service, "match_single", failing)

    job = await_llm_job(client, token, client.post(f"/api/v1/resumes/{rid}/match/v-1", headers=_auth(token)))

    assert job["status"] == "error"
    assert _guest_calls() == 0


def test_cancel_after_model_answer_keeps_call(guest, monkeypatch):
    client, token, rid = guest

    def answered_then_cancelled(**_kwargs):
        raise LlmJobCancelled("Оценка отменена — ответ модели не сохранён", model_answered=True)

    monkeypatch.setattr(match_service, "match_single", answered_then_cancelled)

    job = await_llm_job(client, token, client.post(f"/api/v1/resumes/{rid}/match/v-1", headers=_auth(token)))

    assert job["status"] == "cancelled"
    assert _guest_calls() == 1


def test_enqueue_failure_refunds_call(guest, monkeypatch):
    client, token, rid = guest

    def broken_dispatch(*_args, **_kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(llm_job_service.llm_job_runner, "_dispatch", broken_dispatch)

    with pytest.raises(ConnectionError):
        client.post(f"/api/v1/resumes/{rid}/match/v-1", headers=_auth(token))

    assert _guest_calls() == 0


def test_admin_job_has_no_quota_mark(api_client, monkeypatch):
    def failing(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(match_service, "match_single", failing)
    admin = _register(api_client, "owner@example.com")
    _add_vacancy("v-1")
    rid = api_client.post("/api/v1/resumes", headers=_auth(admin), json={"name": "CV", "text": "python"}).json()["id"]

    job = await_llm_job(api_client, admin, api_client.post(f"/api/v1/resumes/{rid}/match/v-1", headers=_auth(admin)))

    with session_scope() as session:
        assert session.get(LlmJobRow, uuid.UUID(job["id"])).quota_day is None


# --- репозиторий: отмена, recover, сутки списания, повторный возврат ---


def _charged_job(
    session, *, user_id: int, resume_id: int, status: str = "queued", day=None, vacancy_id: str = "v-1"
) -> LlmJobRow:
    day = day or _today()
    assert try_reserve_llm_calls(session, user_id=user_id, calls=1, daily_limit=100, day=day)
    row = lj.create_llm_job(session, user_id=user_id, kind="match", resume_id=resume_id, vacancy_id=vacancy_id)
    row.quota_day = day
    row.status = status
    session.flush()
    return row


@pytest.fixture
def owner(pg_db) -> tuple[int, int]:
    _add_vacancy("v-1")
    with session_scope() as session:
        user_id = new_user(session)
        resume_id = uc.create_resume(session, name="CV", text="python", user_id=user_id).id
    return user_id, resume_id


def _calls(user_id: int, day=None) -> int:
    with session_scope() as session:
        return llm_calls_today(session, user_id=user_id, day=day or _today())


def test_cancel_in_queue_refunds_once(owner):
    user_id, resume_id = owner
    with session_scope() as session:
        job_id = _charged_job(session, user_id=user_id, resume_id=resume_id).id

    with session_scope() as session:
        assert lj.request_llm_job_cancel(session, job_id).status == "cancelled"
    assert _calls(user_id) == 0

    with session_scope() as session:
        assert lj.refund_llm_job_quota(session, job_id) is False
    assert _calls(user_id) == 0


def test_stuck_job_out_of_attempts_refunds(owner, monkeypatch):
    user_id, resume_id = owner
    monkeypatch.setenv("LLM_JOB_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    with session_scope() as session:
        row = _charged_job(session, user_id=user_id, resume_id=resume_id, status="running")
        row.attempts = 1
        row.lease_owner = "gone"
        row.lease_until = datetime.now(UTC) - timedelta(minutes=5)
        job_id = row.id

    with session_scope() as session:
        assert lj.reclaim_expired_llm_job(session, job_id) == "error"
    assert _calls(user_id) == 0


def test_refund_goes_to_day_of_charge(owner):
    user_id, resume_id = owner
    yesterday = _today() - timedelta(days=1)
    with session_scope() as session:
        job_id = _charged_job(session, user_id=user_id, resume_id=resume_id, day=yesterday).id
        assert try_reserve_llm_calls(session, user_id=user_id, calls=1, daily_limit=100, day=_today())

    with session_scope() as session:
        assert lj.refund_llm_job_quota(session, job_id) is True
    assert _calls(user_id, yesterday) == 0
    assert _calls(user_id) == 1


def test_resume_delete_refunds_queued_not_running(owner):
    user_id, resume_id = owner
    _add_vacancy("v-2")
    with session_scope() as session:
        _charged_job(session, user_id=user_id, resume_id=resume_id, status="queued")
        _charged_job(session, user_id=user_id, resume_id=resume_id, status="running", vacancy_id="v-2")
    assert _calls(user_id) == 2

    with session_scope() as session:
        assert uc.delete_resume(session, resume_id, user_id=user_id) is True

    assert _calls(user_id) == 1
