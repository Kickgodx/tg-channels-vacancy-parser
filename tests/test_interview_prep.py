"""Подготовка к собеседованию: LLM-джоба по шаблону адаптации резюме."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from tests.llm_job_helpers import await_llm_job, use_ai_config

from backend.db import repository as repo
from backend.db.models import InterviewPrepRow, VacancyRow
from backend.db.session import get_session_factory, session_scope
from backend.db.transfer import count_replace_losses
from backend.services import interview_prep_service
from jobdesk.ai.clients.openrouter import LLMUsage, OpenRouterClient, _parse_interview_prep
from jobdesk.ai.prompts import INTERVIEW_PREP_PROMPT_VERSION, build_interview_prep_messages
from jobdesk.ai.schema import InterviewPrepExtraction
from jobdesk.config.schema import AiConfig

_ANSWER = {
    "summary": "Будут копать автотесты API на Python.",
    "questions": [
        {
            "kind": "EXPERIENCE",
            "topic": "pytest",
            "question": "Расскажите, как вы строили автотесты API.",
            "why": "В резюме — 3 года pytest",
            "answer_points": ["pytest + requests", "", "фикстуры для данных"],
        },
        {"kind": "странный", "topic": "Kubernetes", "question": "Работали с Kubernetes?", "why": "Требование"},
        {"kind": "technical", "question": ""},
        "мусор",
    ],
    "gaps": [{"skill": "Kubernetes", "advice": "Повторить pod, deployment"}, {"skill": ""}],
    "questions_to_ask": ["Как устроен релизный процесс?", None, ""],
}


# --- ядро


def test_parse_interview_prep_normalizes():
    r = _parse_interview_prep("```json\n" + json.dumps(_ANSWER, ensure_ascii=False) + "\n```")
    assert r.summary.startswith("Будут")
    assert [q.kind for q in r.questions] == ["experience", "technical"]
    assert r.questions[0].answer_points == ["pytest + requests", "фикстуры для данных"]
    assert [g.skill for g in r.gaps] == ["Kubernetes"]
    assert r.questions_to_ask == ["Как устроен релизный процесс?"]


def test_parse_interview_prep_rejects_no_questions():
    with pytest.raises(ValueError):
        _parse_interview_prep('{"summary": "x", "questions": []}')


def test_prompt_marks_missing_skills_as_gap_topics():
    msgs = build_interview_prep_messages(
        resume_text="python, pytest",
        vacancy_text="нужен AQA",
        match_summary={"matching_skills": ["Python"], "missing_skills": ["Kubernetes"]},
    )
    user = msgs[1]["content"]
    assert "python, pytest" in user and "нужен AQA" in user
    assert "Missing skills" in user and "Kubernetes" in user
    assert "NEVER invent" in msgs[0]["content"]
    plain = build_interview_prep_messages(resume_text="r", vacancy_text="v")
    assert "MATCH_CONTEXT" not in plain[1]["content"]


def test_interview_prep_uses_cover_letter_model_and_json_schema(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(model="parse/model", cover_letter_model="writer/model"))
    seen: dict = {}

    def _fake_call(*, messages, parse, model=None, json_schema=None, **_kw):
        seen["model"] = model
        seen["schema"] = json_schema
        return InterviewPrepExtraction(summary="ok"), LLMUsage(model=model or "")

    monkeypatch.setattr(client, "_call_llm", _fake_call)
    result, _ = client.interview_prep(resume_text="r", vacancy_text="v")
    assert result.summary == "ok"
    assert seen["model"] == "writer/model"
    assert seen["schema"]["additionalProperties"] is False


# --- API


def _register(client, email: str) -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _add_vacancy(vacancy_id: str = "vac-1") -> str:
    session = get_session_factory()()
    try:
        session.add(
            VacancyRow(
                id=vacancy_id,
                source_post_key="ch-1",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text="Нужен AQA на Python",
                cleaned_text="Нужен AQA на Python",
            )
        )
        session.commit()
    finally:
        session.close()
    return vacancy_id


def _add_resume(client, token: str) -> int:
    r = client.post("/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": "python, pytest"})
    return r.json()["id"]


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[dict] = []

    class _Client:
        def interview_prep_with_fallback(self, *, resume_text, vacancy_text, match_summary):
            calls.append({"resume": resume_text, "vacancy": vacancy_text, "match": match_summary})
            return InterviewPrepExtraction.model_validate(_ANSWER), LLMUsage(model="fallback/b")

    monkeypatch.setattr(interview_prep_service, "get_interactive_client", lambda: _Client())
    return calls


def test_interview_prep_runs_llm_then_serves_cache(api_client, fake_llm, monkeypatch, tmp_path):
    use_ai_config(monkeypatch, tmp_path, cover_letter_model="writer/a")
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)
    url = f"/api/v1/resumes/{rid}/interview-prep/{vid}"

    assert api_client.get(url, headers=_auth(token)).status_code == 404
    job = await_llm_job(api_client, token, api_client.post(url, headers=_auth(token)))
    assert job["status"] == "done", job
    assert job["kind"] == "interview_prep"
    body = job["interview_prep"]
    assert body["questions"][0]["question"].startswith("Расскажите")
    assert body["gaps"] == [{"skill": "Kubernetes", "advice": "Повторить pod, deployment"}]
    assert (body["cached"], body["stale"]) == (False, False)
    assert fake_llm[0]["resume"] == "python, pytest" and fake_llm[0]["match"] is None

    with session_scope() as session:
        row = session.get(InterviewPrepRow, (rid, vid))
        assert (row.model, row.requested_model, row.prompt_version) == (
            "fallback/b",
            "writer/a",
            INTERVIEW_PREP_PROMPT_VERSION,
        )

    again = api_client.post(url, headers=_auth(token))
    assert again.status_code == 200
    assert again.json()["cached"] is True
    assert len(fake_llm) == 1

    forced = api_client.post(f"{url}?force=true", headers=_auth(token))
    assert await_llm_job(api_client, token, forced)["status"] == "done"
    assert len(fake_llm) == 2

    # Сменили модель писем — подготовка устарела, POST без force пересчитывает.
    use_ai_config(monkeypatch, tmp_path, cover_letter_model="writer/c")
    assert api_client.get(url, headers=_auth(token)).json()["stale"] is True
    regen = api_client.post(url, headers=_auth(token))
    assert regen.status_code == 202
    assert await_llm_job(api_client, token, regen)["status"] == "done"
    assert len(fake_llm) == 3


def test_interview_prep_passes_saved_match_skills(api_client, fake_llm):
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)
    with session_scope() as session:
        repo.upsert_vacancy_match(
            session,
            resume_id=rid,
            vacancy_id=vid,
            overall_score=60,
            confidence=80,
            breakdown={},
            explanation="",
            matching_skills=[{"skill": "Python"}],
            missing_skills=[{"skill": "Kubernetes", "importance": "critical"}],
            strengths=[],
            risks=[],
            model="m",
        )
    r = api_client.post(f"/api/v1/resumes/{rid}/interview-prep/{vid}", headers=_auth(token))
    assert await_llm_job(api_client, token, r)["status"] == "done"
    assert fake_llm[0]["match"] == {"matching_skills": ["Python"], "missing_skills": ["Kubernetes"]}


def test_interview_prep_dropped_on_resume_edit_and_delete(api_client, fake_llm):
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)
    url = f"/api/v1/resumes/{rid}/interview-prep/{vid}"
    assert await_llm_job(api_client, token, api_client.post(url, headers=_auth(token)))["status"] == "done"
    with session_scope() as session:
        assert count_replace_losses(session).interview_preps == 1

    assert api_client.put(f"/api/v1/resumes/{rid}", headers=_auth(token), json={"text": "go"}).status_code == 200
    assert api_client.get(url, headers=_auth(token)).status_code == 404

    assert await_llm_job(api_client, token, api_client.post(url, headers=_auth(token)))["status"] == "done"
    assert api_client.delete(url, headers=_auth(token)).status_code == 204
    assert api_client.delete(url, headers=_auth(token)).status_code == 404


def test_interview_prep_foreign_resume_and_missing_vacancy(api_client, fake_llm):
    owner = _register(api_client, "a@example.com")
    rid = _add_resume(api_client, owner)
    other = _register(api_client, "b@example.com")
    vid = _add_vacancy()
    assert api_client.post(f"/api/v1/resumes/{rid}/interview-prep/{vid}", headers=_auth(other)).status_code == 404
    assert api_client.post(f"/api/v1/resumes/{rid}/interview-prep/nope", headers=_auth(owner)).status_code == 404
    assert fake_llm == []
