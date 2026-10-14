"""Общий лимит запросов к LLM (Redis) и суточная квота вызовов на пользователя."""

from __future__ import annotations

from datetime import date

import fakeredis
import pytest
from tests.llm_job_helpers import await_llm_job
from tests.test_llm_jobs import _add_vacancy, _auth, _register

from backend.db.llm_usage_repository import llm_calls_today, try_reserve_llm_calls
from backend.db.session import init_db, reset_engine, session_scope
from backend.services import llm_rate_gate, match_service
from backend.services.llm_rate_gate import RedisWindowRateGate
from backend.settings import get_settings
from tg_jobs_parser.ai import rate_limit
from tg_jobs_parser.ai.rate_limit import LlmQuotaExhausted, OpenRouterRateLimiter, register_global_rate_gate

# --- общий лимит (Redis) ---


def test_redis_gate_counts_per_minute_window(monkeypatch):
    server = fakeredis.FakeStrictRedis()
    gate = RedisWindowRateGate(lambda: server)
    clock = [600.0]  # начало 10-й минуты
    monkeypatch.setattr(llm_rate_gate.time, "time", lambda: clock[0])

    assert gate.wait_seconds("k", 2) == 0
    assert gate.wait_seconds("k", 2) == 0
    clock[0] = 615.0
    assert gate.wait_seconds("k", 2) == pytest.approx(45.0)  # до следующего окна
    assert gate.wait_seconds("other", 2) == 0  # свой счётчик на ключ
    clock[0] = 661.0
    assert gate.wait_seconds("k", 2) == 0


def test_two_gates_share_one_budget():
    """Две «реплики» с одним Redis делят RPM, а не удваивают его."""
    server = fakeredis.FakeStrictRedis()
    a, b = RedisWindowRateGate(lambda: server), RedisWindowRateGate(lambda: server)
    allowed = sum(1 for g in (a, b, a, b, a, b) if g.wait_seconds("k", 3) == 0)
    assert allowed == 3


class _Gate:
    def __init__(self, waits):
        self.waits = list(waits)
        self.calls = 0

    def wait_seconds(self, key, per_minute):
        self.calls += 1
        w = self.waits.pop(0)
        if isinstance(w, Exception):
            raise w
        return w


@pytest.fixture
def limiter(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(rate_limit.time, "sleep", slept.append)
    lim = OpenRouterRateLimiter(requests_per_minute=600, max_retries_on_429=0, retry_base_delay_seconds=1)
    yield lim, slept
    register_global_rate_gate(None)


def test_limiter_waits_for_global_slot(limiter):
    lim, slept = limiter
    gate = _Gate([12.0, 3.0, 0.0])
    register_global_rate_gate(gate)
    lim.acquire()
    assert gate.calls == 3
    assert slept[-2:] == [5.0, 3.0]  # ожидание шагами, между ними — повторная попытка


def test_limiter_fails_open_when_gate_unavailable(limiter):
    lim, _ = limiter
    gate = _Gate([ConnectionError("redis down"), ConnectionError("redis down")])
    register_global_rate_gate(gate)
    lim.acquire()
    lim.acquire()
    assert gate.calls == 2


def test_limiter_stops_waiting_when_llm_disabled(limiter):
    lim, _ = limiter

    class _DisablingGate:
        def wait_seconds(self, key, per_minute):
            lim.disable_llm_for_run("квота")
            return 30.0

    register_global_rate_gate(_DisablingGate())
    with pytest.raises(LlmQuotaExhausted):
        lim.acquire()


def test_no_gate_without_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()
    try:
        assert llm_rate_gate.register_redis_rate_gate() is False
        assert rate_limit._global_gate is None
    finally:
        get_settings.cache_clear()


# --- суточная квота ---


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'quota.db').as_posix()}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_reserve_respects_daily_limit(db):
    day = date(2026, 10, 14)
    with session_scope() as s:
        assert try_reserve_llm_calls(s, user_id=1, calls=2, daily_limit=3, day=day)
        assert try_reserve_llm_calls(s, user_id=1, calls=1, daily_limit=3, day=day)
        assert not try_reserve_llm_calls(s, user_id=1, calls=1, daily_limit=3, day=day)
        assert not try_reserve_llm_calls(s, user_id=2, calls=4, daily_limit=3, day=day)
        assert try_reserve_llm_calls(s, user_id=2, calls=3, daily_limit=3, day=day)
        assert try_reserve_llm_calls(s, user_id=1, calls=1, daily_limit=3, day=date(2026, 10, 15))
    with session_scope() as s:
        assert llm_calls_today(s, user_id=1, day=day) == 3  # отказ ничего не списал


def _fake_match(*, resume_id, resume_text, vacancy_id, vacancy_text):
    from backend.db import repository as repo

    with session_scope() as session:
        return repo.upsert_vacancy_match(
            session,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            overall_score=50,
            confidence=50,
            breakdown={
                "role_match": 50,
                "skills_match": 50,
                "seniority_match": 50,
                "domain_match": 50,
                "work_format_match": 50,
            },
            explanation="ok",
            matching_skills=[],
            missing_skills=[],
            strengths=[],
            risks=[],
            model="m",
        )


def _match_three_times(client, token: str) -> list:
    rid = client.post("/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python"}).json()["id"]
    out = []
    for i in range(3):
        r = client.post(f"/api/v1/resumes/{rid}/match/q-{i}", headers=_auth(token))
        if r.status_code == 202:
            await_llm_job(client, token, r)
        out.append(r)
    return out


def test_daily_quota_limits_non_admin_jobs(api_client, monkeypatch):
    monkeypatch.setenv("LLM_USER_DAILY_CALLS", "2")
    get_settings.cache_clear()
    monkeypatch.setattr(match_service, "match_single", _fake_match)
    for i in range(3):
        _add_vacancy(f"q-{i}")
    _register(api_client, "owner@example.com")  # первый — admin
    user = _register(api_client, "guest@example.com")

    responses = _match_three_times(api_client, user)

    assert [r.status_code for r in responses] == [202, 202, 429]
    detail = responses[2].json()["detail"]
    assert detail["code"] == "LLM_QUOTA_EXCEEDED"
    assert detail["retryable"] is False
    # Кэшированный результат квоту не тратит.
    rid = api_client.get("/api/v1/resumes", headers=_auth(user)).json()["items"][0]["id"]
    assert api_client.post(f"/api/v1/resumes/{rid}/match/q-0", headers=_auth(user)).status_code == 200


def test_admin_is_exempt_from_daily_quota(api_client, monkeypatch):
    monkeypatch.setenv("LLM_USER_DAILY_CALLS", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(match_service, "match_single", _fake_match)
    for i in range(3):
        _add_vacancy(f"q-{i}")
    admin = _register(api_client, "owner@example.com")

    assert [r.status_code for r in _match_three_times(api_client, admin)] == [202, 202, 202]
