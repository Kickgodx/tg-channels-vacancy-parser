"""Сопроводительные письма: кэш, force, доступ, модель и промпт генерации."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.llm_job_helpers import await_llm_job

from backend.db.models import CoverLetterRow, VacancyRow
from backend.db.session import get_session_factory, session_scope
from backend.services import cover_letter_service
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
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text=text,
                cleaned_text=text,
            )
        )
        session.commit()
    finally:
        session.close()
    return vacancy_id


def _add_resume(client, token: str, text: str = "python dev") -> int:
    return client.post("/api/v1/resumes", headers=_auth(token), json={"name": "CV", "text": text}).json()["id"]


@pytest.fixture
def stub_llm(monkeypatch):
    """Подменяет LLM-вызов: считает обращения, возвращает фиксированное письмо."""
    calls: list[dict] = []

    def _fake(
        *,
        resume_id,
        resume_text,
        vacancy_id,
        vacancy_text,
        tone="neutral",
        extra_notes=None,
    ):
        calls.append({"resume_id": resume_id, "vacancy_id": vacancy_id, "tone": tone})
        from backend.db import repository as repo

        with session_scope() as session:
            return repo.upsert_cover_letter(
                session,
                resume_id=resume_id,
                vacancy_id=vacancy_id,
                text=f"Здравствуйте! Письмо #{len(calls)} под вашу вакансию.",
                tone=tone,
                model="letter/model",
            )

    monkeypatch.setattr(cover_letter_service, "generate_single", _fake)
    return calls


def test_cover_letter_runs_llm_then_serves_cache(api_client, stub_llm):
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)

    r = api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token), json={})
    job = await_llm_job(api_client, token, r)
    assert job["status"] == "done"
    body = job["cover_letter"]
    assert body["text"] == "Здравствуйте! Письмо #1 под вашу вакансию."
    assert body["tone"] == "neutral"
    assert body["model"] == "letter/model"
    assert body["cached"] is False
    assert len(stub_llm) == 1

    # повторный POST без force — из БД, без вызова LLM
    again = api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token), json={})
    assert again.status_code == 200
    assert again.json()["cached"] is True
    assert len(stub_llm) == 1

    # force=true + другой тон — новый вызов и перезапись строки
    forced = api_client.post(
        f"/api/v1/resumes/{rid}/cover-letter/{vid}?force=true",
        headers=_auth(token),
        json={"tone": "formal"},
    )
    forced_job = await_llm_job(api_client, token, forced)
    assert forced_job["cover_letter"]["text"] == "Здравствуйте! Письмо #2 под вашу вакансию."
    assert forced_job["cover_letter"]["tone"] == "formal"
    assert forced_job["cover_letter"]["cached"] is False
    assert len(stub_llm) == 2

    # GET отдаёт сохранённый вариант
    got = api_client.get(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["text"] == "Здравствуйте! Письмо #2 под вашу вакансию."
    assert got.json()["tone"] == "formal"
    assert got.json()["cached"] is True


def test_cover_letter_404_and_auth(api_client, stub_llm):
    t1 = _register(api_client, "a@example.com")
    t2 = _register(api_client, "b@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, t1)

    assert api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}").status_code == 401
    # чужое резюме
    assert api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(t2)).status_code == 404
    # несуществующая вакансия
    assert api_client.post(f"/api/v1/resumes/{rid}/cover-letter/nope", headers=_auth(t1)).status_code == 404
    # письма ещё нет
    assert api_client.get(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(t1)).status_code == 404
    assert stub_llm == []


def test_cover_letter_delete(api_client, stub_llm):
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)

    r = api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token), json={})
    await_llm_job(api_client, token, r)
    deleted = api_client.delete(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token))
    assert deleted.status_code == 204
    assert api_client.get(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token)).status_code == 404
    # повторный delete — 404
    assert api_client.delete(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token)).status_code == 404


def test_cover_letter_removed_with_resume(api_client, stub_llm):
    """Удаление резюме сносит и сохранённые письма (как vacancy_matches)."""
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)
    r = api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token), json={})
    await_llm_job(api_client, token, r)

    session = get_session_factory()()
    try:
        assert session.get(CoverLetterRow, (rid, vid)) is not None
    finally:
        session.close()

    assert api_client.delete(f"/api/v1/resumes/{rid}", headers=_auth(token)).status_code == 204

    session = get_session_factory()()
    try:
        assert session.get(CoverLetterRow, (rid, vid)) is None
    finally:
        session.close()


def test_cover_letter_reports_llm_failure(api_client, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("OpenRouter API: 503")

    monkeypatch.setattr(cover_letter_service, "generate_single", _boom)
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)

    r = api_client.post(f"/api/v1/resumes/{rid}/cover-letter/{vid}", headers=_auth(token), json={})
    job = await_llm_job(api_client, token, r)
    assert job["status"] == "error"
    assert "OpenRouter" in (job.get("error_message") or "")


def test_cover_letter_end_to_end(api_client, monkeypatch):
    """Реальный cover_letter_service.generate_single со stubbed interactive client."""
    from tg_jobs_parser.ai.clients.openrouter import LLMUsage

    class _FakeClient:
        def cover_letter_with_fallback(self, *, resume_text, vacancy_text, tone, extra_notes, match_summary):
            assert "python" in resume_text
            assert "Python-разработчик" in vacancy_text
            assert tone == "friendly"
            return "Привет! Пишу по вакансии Python-разработчика.", LLMUsage(model="m1")

    monkeypatch.setattr(cover_letter_service, "get_interactive_client", lambda: _FakeClient())

    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)

    r = api_client.post(
        f"/api/v1/resumes/{rid}/cover-letter/{vid}",
        headers=_auth(token),
        json={"tone": "friendly"},
    )
    job = await_llm_job(api_client, token, r)
    assert job["status"] == "done", job
    body = job["cover_letter"]
    assert body["text"] == "Привет! Пишу по вакансии Python-разработчика."
    assert body["tone"] == "friendly"
    assert body["model"] == "m1"


def test_cover_letter_model_falls_back_to_match_then_parse():
    assert AiConfig(model="parse/model").resolved_cover_letter_model() == "parse/model"
    assert (
        AiConfig(model="parse/model", match_model="reasoning/model").resolved_cover_letter_model() == "reasoning/model"
    )
    assert (
        AiConfig(
            model="parse/model",
            match_model="reasoning/model",
            cover_letter_model="writer/model",
        ).resolved_cover_letter_model()
        == "writer/model"
    )


def test_cover_letter_uses_own_model_without_json_format(monkeypatch):
    """cover_letter() зовёт свою модель в plain-text режиме (без response_format)."""
    from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    ai = AiConfig(
        model="parse/model",
        match_model="reasoning/model",
        cover_letter_model="writer/model",
    )
    client = OpenRouterClient(ai)
    seen: dict = {}

    def _fake_call(
        *,
        messages,
        parse,
        model=None,
        reasoning=None,
        reasoning_effort="",
        json_schema=None,
        plain_text=False,
    ):
        seen["model"] = model
        seen["plain_text"] = plain_text
        return "Текст письма", LLMUsage(model=model or "")

    monkeypatch.setattr(client, "_call_llm", _fake_call)
    text, _ = client.cover_letter(resume_text="python", vacancy_text="нужен python")

    assert text == "Текст письма"
    assert seen["model"] == "writer/model"
    assert seen["plain_text"] is True


def test_cover_letter_prompt_contains_inputs_and_tone():
    from tg_jobs_parser.ai.prompts import build_cover_letter_messages

    msgs = build_cover_letter_messages(
        resume_text="Опыт: Python 5 лет",
        vacancy_text="Ищем Python-разработчика, удалёнка",
        tone="formal",
        extra_notes="упомяни open-source",
        match_summary={
            "score": 82,
            "strengths": ["Совпадает стек"],
            "matching_skills": ["Python"],
            "explanation": "Хорошо подходит",
        },
    )
    assert len(msgs) == 2
    user = msgs[1]["content"]
    assert "Опыт: Python 5 лет" in user
    assert "Ищем Python-разработчика" in user
    assert "formal" in user
    assert "упомяни open-source" in user
    assert "82/100" in user
    assert "Совпадает стек" in user


def test_parse_cover_letter_strips_fence():
    from tg_jobs_parser.ai.clients.openrouter import _parse_cover_letter

    assert _parse_cover_letter("  Текст письма  ") == "Текст письма"
    assert _parse_cover_letter("```\nТекст письма\n```") == "Текст письма"
    with pytest.raises(ValueError):
        _parse_cover_letter("   ")
