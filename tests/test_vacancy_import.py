"""Ручной импорт вакансий текстом: rules-разбор, дедуп по хэшу, LLM-ветка, auth."""

from __future__ import annotations

from typing import ClassVar

import pytest
from tests.llm_job_helpers import await_llm_job

from backend.services import import_service

VACANCY_TEXT = (
    "Вакансия: Senior Python-разработчик в компанию Acme.\n"
    "Зарплата: от 250 000 до 350 000 руб. на руки.\n"
    "Формат: удалёнка. Нужен опыт с PostgreSQL и Docker.\n"
    "Контакт: @recruiter_acme"
)
NOT_VACANCY_TEXT = "Сегодня хорошая погода, собираемся на пикник у реки."


def _register(client, email: str, password: str = "secret1") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _import(client, token, texts, **kw):
    return client.post(
        "/api/v1/vacancies/import",
        headers=_auth(token),
        json={"texts": texts, **kw},
    )


def test_import_creates_vacancy_and_dedups(api_client):
    token = _register(api_client, "imp@example.com")

    r = _import(
        api_client,
        token,
        [VACANCY_TEXT],
        source_url="https://hh.ru/vacancy/123",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["existing"] == 0
    assert body["ai_enabled"] is False

    item = body["items"][0]
    assert item["status"] == "created"
    v = item["vacancy"]
    assert v["source_channel"] == "manual"
    assert v["post_url"] == "https://hh.ru/vacancy/123"
    assert "Python" in v["detected_specializations"]

    # видна в общем списке по фильтру канала
    lst = api_client.get("/api/v1/vacancies?source_channel=manual", headers=_auth(token))
    assert lst.status_code == 200
    assert lst.json()["total"] == 1

    # повторный импорт того же текста — дедуп, новой записи нет
    again = _import(api_client, token, [VACANCY_TEXT])
    assert again.json()["items"][0]["status"] == "exists"
    assert again.json()["existing"] == 1
    lst2 = api_client.get("/api/v1/vacancies?source_channel=manual", headers=_auth(token))
    assert lst2.json()["total"] == 1


def test_import_rules_mode_saves_any_text(api_client):
    """Без LLM score-гейт отключён: вставленный текст сохраняется как есть."""
    token = _register(api_client, "imp@example.com")
    r = _import(api_client, token, [NOT_VACANCY_TEXT])
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["status"] == "created"
    assert item["vacancy"]["source_channel"] == "manual"


def test_import_batch_and_empty(api_client):
    token = _register(api_client, "imp@example.com")
    r = _import(api_client, token, [VACANCY_TEXT, "   ", NOT_VACANCY_TEXT])
    body = r.json()
    assert [it["status"] for it in body["items"]] == ["created", "error", "created"]

    empty = _import(api_client, token, ["", "   "])
    assert empty.status_code == 400


def test_import_requires_auth(api_client):
    assert api_client.post("/api/v1/vacancies/import", json={"texts": [VACANCY_TEXT]}).status_code == 401


def test_import_is_admin_only(api_client):
    _register(api_client, "owner@example.com")  # первый — admin
    token = _register(api_client, "guest@example.com")
    r = _import(api_client, token, [VACANCY_TEXT])
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FORBIDDEN"


class _FakeAI:
    """AIEnrichmentService без сети: «пикник» — не вакансия, остальное — hybrid."""

    calls: ClassVar[list[str]] = []

    def __init__(self, config):
        from tg_jobs_parser.ai.enricher import RunAIStats

        self.stats = RunAIStats()

    def begin_batch(self, total):
        pass

    def enrich(self, vacancy, *, cancel=None):
        self.stats.calls += 1
        _FakeAI.calls.append(vacancy.id)
        if "пикник" in vacancy.cleaned_text:
            self.stats.dropped_not_job += 1
            return None
        return vacancy.model_copy(update={"extraction_source": "hybrid", "ai_confidence": 0.9})

    def log_batch_finished(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_ai(monkeypatch, default_test_config) -> list[str]:
    """ai.enabled + фейковый LLM; возвращает id вакансий, ушедших в модель."""
    from tg_jobs_parser.config.loader import load_config

    real_cfg = load_config(default_test_config)
    ai_cfg = real_cfg.model_copy(update={"ai": real_cfg.ai.model_copy(update={"enabled": True})})
    monkeypatch.setattr(import_service, "load_config", lambda *a, **k: ai_cfg)
    monkeypatch.setattr("tg_jobs_parser.ai.enricher.AIEnrichmentService", _FakeAI, raising=True)
    monkeypatch.setattr(_FakeAI, "calls", [])
    return _FakeAI.calls


def test_import_with_ai_runs_as_llm_job(api_client, fake_ai):
    """ai.enabled → 202 и LLM-джоба; None от LLM = «не вакансия»."""
    token = _register(api_client, "imp@example.com")
    r = _import(api_client, token, [VACANCY_TEXT, NOT_VACANCY_TEXT])
    assert r.status_code == 202, r.text

    job = await_llm_job(api_client, token, r)
    assert job["status"] == "done", job
    assert job["kind"] == "import"
    assert job["resume_id"] is None and job["vacancy_id"] is None
    body = job["import_result"]
    assert body["ai_enabled"] is True
    assert (body["total"], body["created"], body["rejected"]) == (2, 1, 1)
    created, rejected = body["items"]
    assert created["status"] == "created"
    assert created["vacancy"]["extraction_source"] == "hybrid"
    assert rejected["status"] == "not_vacancy"
    assert "не похож" in rejected["message"]
    # score-гейт перед LLM отключён: оба текста дошли до модели
    assert len(fake_ai) == 2


def test_import_with_ai_is_sync_when_every_text_exists(api_client, fake_ai):
    """Все тексты уже в базе — LLM не нужен: сразу 200, без джобы."""
    token = _register(api_client, "imp@example.com")
    assert await_llm_job(api_client, token, _import(api_client, token, [VACANCY_TEXT]))["status"] == "done"
    fake_ai.clear()

    r = _import(api_client, token, [VACANCY_TEXT, "   "])

    assert r.status_code == 200, r.text
    assert [it["status"] for it in r.json()["items"]] == ["exists", "error"]
    assert r.json()["ai_enabled"] is True
    assert fake_ai == []


def test_import_job_replays_by_idempotency_key(api_client, fake_ai):
    token = _register(api_client, "imp@example.com")
    headers = {**_auth(token), "Idempotency-Key": "imp-1"}
    first = api_client.post("/api/v1/vacancies/import", headers=headers, json={"texts": [VACANCY_TEXT]})
    again = api_client.post("/api/v1/vacancies/import", headers=headers, json={"texts": [VACANCY_TEXT]})

    assert first.status_code == again.status_code == 202
    assert first.json()["job_id"] == again.json()["job_id"]
    assert await_llm_job(api_client, token, first)["import_result"]["created"] == 1


def test_import_falls_back_to_rules_when_daily_quota_ends(api_client, monkeypatch, fake_ai):
    """Квота не-админа кончилась посреди импорта — остальные тексты разбираются правилами.

    Через API импорт только у админа (без квоты); джоба не-админа — например, если
    права сняли после постановки, — по-прежнему упирается в квоту.
    """
    from backend.db.models import VacancyRow
    from backend.db.session import session_scope
    from backend.settings import get_settings

    monkeypatch.setenv("LLM_USER_DAILY_CALLS", "1")
    get_settings.cache_clear()
    job_id, user_id = _running_import_job([VACANCY_TEXT, "Вакансия: QA-инженер, удалёнка, Selenium и pytest."])

    ok = import_service.run_import_job(
        job_id, user_id=user_id, params=_job_row(job_id).params, result=None, lease_owner="owner-1"
    )

    assert ok is True
    body = _job_row(job_id).result
    assert len(fake_ai) == 1
    assert [it["status"] for it in body["items"]] == ["created", "created"]
    with session_scope() as session:
        sources = [session.get(VacancyRow, it["vacancy_id"]).extraction_source for it in body["items"]]
    assert sources == ["hybrid", "rules"]
    assert "квота" in body["warning"]


def _running_import_job(texts: list[str], *, result: dict | None = None) -> tuple:
    """Строка llm_jobs ``import`` в состоянии running со своим lease (как после claim)."""
    from backend.db import llm_job_repository as lj
    from backend.db.models import UserRow
    from backend.db.session import session_scope

    with session_scope() as session:
        user = UserRow(email="w@example.com", password_hash="x")
        session.add(user)
        session.flush()
        row = lj.create_llm_job(session, user_id=user.id, kind="import", params=import_service.job_params(texts, None))
        row.result = result
        job_id, user_id = row.id, user.id
    with session_scope() as session:
        assert lj.claim_llm_job(session, job_id, lease_owner="owner-1") is not None
    return job_id, user_id


def _job_row(job_id):
    from backend.db import llm_job_repository as lj
    from backend.db.session import session_scope

    with session_scope() as session:
        row = lj.get_llm_job(session, job_id)
        session.expunge(row)
        return row


def test_import_job_retry_skips_already_processed_texts(api_client, fake_ai):
    """Ретрай после падения воркера продолжает с первого текста без итога — LLM не повторяется."""
    done_item = {"index": 0, "status": "not_vacancy", "message": "LLM: текст не похож на вакансию"}
    partial = {"total": 2, "ai_enabled": True, "warning": None, "items": [done_item]}
    job_id, user_id = _running_import_job([NOT_VACANCY_TEXT, VACANCY_TEXT], result=partial)

    ok = import_service.run_import_job(
        job_id, user_id=user_id, params=_job_row(job_id).params, result=partial, lease_owner="owner-1"
    )

    assert ok is True
    assert len(fake_ai) == 1  # только второй текст
    items = _job_row(job_id).result["items"]
    assert [(it["index"], it["status"]) for it in items] == [(0, "not_vacancy"), (1, "created")]


def test_import_job_stops_when_lease_is_lost(api_client, fake_ai):
    from sqlalchemy import func, select

    from backend.db.models import VacancyRow
    from backend.db.session import session_scope

    job_id, user_id = _running_import_job([VACANCY_TEXT])

    ok = import_service.run_import_job(
        job_id, user_id=user_id, params=_job_row(job_id).params, result=None, lease_owner="someone-else"
    )

    assert ok is False
    assert _job_row(job_id).result is None
    with session_scope() as session:
        # Вакансия пишется одной транзакцией с итогом — без lease не пишется и она.
        assert session.scalar(select(func.count()).select_from(VacancyRow)) == 0


def test_import_job_rq_timeout_grows_with_texts():
    from backend.services.llm_job_service import IMPORT_SECONDS_PER_TEXT, rq_job_timeout
    from backend.settings import get_settings

    base = get_settings().llm_rq_job_timeout_seconds
    assert rq_job_timeout("match", None) == base
    assert rq_job_timeout("import", {"texts": ["a"] * 50}) == base + 50 * IMPORT_SECONDS_PER_TEXT
