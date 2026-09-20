"""Соло-матчинг одной вакансии из карточки: кэш, force, доступ, модель матчинга."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.db.models import VacancyRow
from backend.db.session import get_session_factory
from backend.services import match_service
from tg_jobs_parser.config.schema import AiConfig


def _register(client, email: str, password: str = "secret1") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_vacancy(vacancy_id: str = "vac-1", text: str = "Нужен Python-разработчик") -> str:
    factory = get_session_factory()
    session = factory()
    try:
        session.add(
            VacancyRow(
                id=vacancy_id,
                source_post_key="ch-1",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
                raw_text=text,
                cleaned_text=text,
            )
        )
        session.commit()
    finally:
        session.close()
    return vacancy_id


@pytest.fixture
def stub_llm(monkeypatch):
    """Подменяет LLM-вызов: считает обращения, возвращает фиксированную оценку."""
    calls: list[dict] = []

    def _fake(session, *, resume_id, resume_text, vacancy_id, vacancy_text):
        calls.append({"resume_id": resume_id, "vacancy_id": vacancy_id})
        from backend.db import repository as repo

        return repo.upsert_vacancy_match(
            session,
            resume_id=resume_id,
            vacancy_id=vacancy_id,
            overall_score=77 + len(calls),
            confidence=90,
            breakdown={
                "role_match": 90,
                "skills_match": 75,
                "seniority_match": 85,
                "domain_match": 100,
                "work_format_match": 100,
            },
            explanation="Подходит по стеку, не хватает k8s",
            matching_skills=[
                {"skill": "python", "importance": "critical", "evidence": "В резюме есть Python."}
            ],
            missing_skills=[
                {"skill": "k8s", "importance": "important", "evidence": "В резюме не указан."}
            ],
            strengths=["Совпадает основной стек."],
            risks=["Опыт с Kubernetes в резюме не указан."],
            model="reasoning/model",
        )

    monkeypatch.setattr(match_service, "match_single", _fake)
    return calls


def test_solo_match_runs_llm_then_serves_cache(api_client, stub_llm):
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = api_client.post(
        "/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python dev"}
    ).json()["id"]

    r = api_client.post(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall_score"] == 78
    assert body["confidence"] == 90
    assert body["breakdown"]["role_match"] == 90
    assert body["cached"] is False
    assert body["matching_skills"][0]["skill"] == "python"
    assert body["matching_skills"][0]["importance"] == "critical"
    assert body["missing_skills"][0]["skill"] == "k8s"
    assert body["strengths"] == ["Совпадает основной стек."]
    assert body["model"] == "reasoning/model"
    assert len(stub_llm) == 1

    # повторный POST без force — из БД, без вызова LLM
    again = api_client.post(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(token))
    assert again.json()["cached"] is True
    assert again.json()["overall_score"] == 78
    assert len(stub_llm) == 1

    # force=true — новый вызов и новая оценка
    forced = api_client.post(
        f"/api/v1/resumes/{rid}/match/{vid}?force=true", headers=_auth(token)
    )
    assert forced.json()["overall_score"] == 79
    assert forced.json()["cached"] is False
    assert len(stub_llm) == 2

    # GET отдаёт сохранённую оценку
    got = api_client.get(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["overall_score"] == 79
    assert got.json()["cached"] is True


def test_solo_match_404_and_auth(api_client, stub_llm):
    t1 = _register(api_client, "a@example.com")
    t2 = _register(api_client, "b@example.com")
    vid = _add_vacancy()
    rid = api_client.post(
        "/api/v1/resumes", headers=_auth(t1), json={"name": "CV", "text": "python"}
    ).json()["id"]

    assert api_client.post(f"/api/v1/resumes/{rid}/match/{vid}").status_code == 401
    # чужое резюме
    assert (
        api_client.post(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(t2)).status_code
        == 404
    )
    # несуществующая вакансия
    assert (
        api_client.post(f"/api/v1/resumes/{rid}/match/nope", headers=_auth(t1)).status_code
        == 404
    )
    # оценки ещё нет
    assert (
        api_client.get(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(t1)).status_code
        == 404
    )
    assert stub_llm == []


def test_match_single_end_to_end(api_client, monkeypatch):
    """Реальный match_service.match_single со stubbed OpenRouterClient:
    extraction → calculate_overall_score → vacancy_matches → API response."""
    from tg_jobs_parser.ai.clients.openrouter import LLMUsage
    from tg_jobs_parser.ai.schema import (
        MatchBreakdown,
        SkillMatch,
        VacancyMatchExtraction,
    )

    class _FakeClient:
        def __init__(self, ai):
            pass

        def match_with_fallback(self, *, resume_text, vacancy_text):
            extraction = VacancyMatchExtraction(
                confidence=88,
                breakdown=MatchBreakdown(
                    role_match=90, skills_match=70, seniority_match=80,
                    domain_match=100, work_format_match=100,
                ),
                matching_skills=[
                    SkillMatch(skill="Python", importance="critical",
                               evidence="В резюме указан Python.")
                ],
                missing_skills=[
                    SkillMatch(skill="Kubernetes", importance="important",
                               evidence="Опыт с Kubernetes в резюме не указан.")
                ],
                strengths=["Совпадает основной стек."],
                risks=["Опыт с Kubernetes в резюме не указан."],
                explanation="Кандидат соответствует роли, пробел — Kubernetes.",
            )
            return extraction, LLMUsage(model="m1")

        def close(self):
            pass

    monkeypatch.setattr(match_service, "OpenRouterClient", _FakeClient)

    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = api_client.post(
        "/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python"}
    ).json()["id"]

    r = api_client.post(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    # 90*0.3 + 70*0.35 + 80*0.2 + 100*0.1 + 100*0.05 = 82.5 → 82 (banker's round)
    assert body["overall_score"] == 82
    assert body["confidence"] == 88
    assert body["breakdown"]["skills_match"] == 70
    assert body["matching_skills"][0]["importance"] == "critical"
    assert body["explanation"] == "Кандидат соответствует роли, пробел — Kubernetes."


def test_solo_match_reports_llm_failure(api_client, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("OpenRouter API: 503")

    monkeypatch.setattr(match_service, "match_single", _boom)
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = api_client.post(
        "/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python"}
    ).json()["id"]

    r = api_client.post(f"/api/v1/resumes/{rid}/match/{vid}", headers=_auth(token))
    assert r.status_code == 502
    assert "OpenRouter" in r.json()["detail"]


def test_match_model_falls_back_to_parse_model():
    assert AiConfig(model="parse/model").resolved_match_model() == "parse/model"
    assert (
        AiConfig(model="parse/model", match_model="reasoning/model").resolved_match_model()
        == "reasoning/model"
    )


def test_match_uses_match_model_and_reasoning(monkeypatch):
    """match() должен звать модель матчинга с её собственным флагом reasoning."""
    from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    ai = AiConfig(
        model="parse/model",
        match_model="reasoning/model",
        match_reasoning_enabled=True,
        reasoning_enabled=False,
    )
    client = OpenRouterClient(ai)
    seen: dict = {}

    def _fake_call(*, messages, parse, model=None, reasoning=None, json_schema=None):
        seen["model"] = model
        seen["reasoning"] = reasoning
        return object(), LLMUsage(model=model or "")

    monkeypatch.setattr(client, "_call_llm", _fake_call)
    client.match(resume_text="python", vacancy_text="нужен python")

    assert seen["model"] == "reasoning/model"
    assert seen["reasoning"] is True


def test_match_retries_once_on_invalid_json(monkeypatch):
    """Невалидный JSON → один controlled retry той же модели, затем успех."""
    import json as _json

    from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient
    from tg_jobs_parser.ai.schema import VacancyMatchExtraction

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(model="m"))
    attempts: list[int] = []

    def _flaky(*, messages, parse, model=None, reasoning=None, json_schema=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise _json.JSONDecodeError("bad", "doc", 0)
        return VacancyMatchExtraction(confidence=80), LLMUsage(model=model or "")

    monkeypatch.setattr(client, "_call_llm", _flaky)
    extraction, _ = client.match(resume_text="python", vacancy_text="нужен python")
    assert extraction.confidence == 80
    assert len(attempts) == 2

    # обе попытки провалились — ошибка пробрасывается (дальше fallback-цепочка)
    def _always_bad(*, messages, parse, model=None, reasoning=None, json_schema=None):
        raise ValueError("broken")

    monkeypatch.setattr(client, "_call_llm", _always_bad)
    with pytest.raises(ValueError):
        client.match(resume_text="python", vacancy_text="нужен python")
