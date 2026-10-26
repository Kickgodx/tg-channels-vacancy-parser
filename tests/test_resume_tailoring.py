"""Адаптация резюме под вакансию (LLM-джоба по шаблону писем) и разбор резюме из PDF/DOCX."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from tests.llm_job_helpers import await_llm_job, use_ai_config

from backend.db.models import ResumeTailoringRow, VacancyRow
from backend.db.session import get_session_factory, session_scope
from backend.services import resume_tailoring_service
from tg_jobs_parser.ai.clients.openrouter import LLMUsage, OpenRouterClient, _parse_tailoring
from tg_jobs_parser.ai.prompts import RESUME_TAILOR_PROMPT_VERSION, build_resume_tailor_messages
from tg_jobs_parser.ai.schema import ResumeTailoringExtraction
from tg_jobs_parser.config.schema import AiConfig
from tg_jobs_parser.utils.resume_file import ResumeFileError, extract_resume_text

_ANSWER = {
    "summary": "Поднимите автотесты на Python выше.",
    "suggestions": [
        {
            "section": "Навыки",
            "current": "pytest",
            "suggested": "Python, pytest — 3 года автотестов API",
            "reason": "Вакансия требует автотесты API на Python",
            "priority": "HIGH",
        },
        "мусор",
    ],
    "keywords": ["CI/CD"],
    "do_not_claim": ["Kubernetes"],
}


# --- ядро


def test_parse_tailoring_normalizes():
    r = _parse_tailoring("```json\n" + json.dumps(_ANSWER, ensure_ascii=False) + "\n```")
    assert r.summary.startswith("Поднимите")
    assert len(r.suggestions) == 1
    assert r.suggestions[0].priority == "high"
    assert r.do_not_claim == ["Kubernetes"]


def test_parse_tailoring_rejects_empty():
    with pytest.raises(ValueError):
        _parse_tailoring('{"summary": "", "suggestions": []}')


def test_prompt_puts_missing_skills_to_do_not_claim_context():
    msgs = build_resume_tailor_messages(
        resume_text="python, pytest",
        vacancy_text="нужен AQA",
        match_summary={"matching_skills": ["Python"], "missing_skills": ["Kubernetes"]},
    )
    user = msgs[1]["content"]
    assert "python, pytest" in user and "нужен AQA" in user
    assert "Missing skills" in user and "Kubernetes" in user
    assert "NEVER add skills" in msgs[0]["content"]


def test_tailor_uses_cover_letter_model_and_json_schema(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(AiConfig(model="parse/model", cover_letter_model="writer/model"))
    seen: dict = {}

    def _fake_call(*, messages, parse, model=None, json_schema=None, **_kw):
        seen["model"] = model
        seen["schema"] = json_schema
        return ResumeTailoringExtraction(summary="ok"), LLMUsage(model=model or "")

    monkeypatch.setattr(client, "_call_llm", _fake_call)
    result, _ = client.tailor_resume(resume_text="r", vacancy_text="v")
    assert result.summary == "ok"
    assert seen["model"] == "writer/model"
    assert seen["schema"]["additionalProperties"] is False


# --- файлы


def _pdf(text: str) -> bytes:
    """Минимальный PDF с одной страницей текста (Helvetica, ASCII)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _docx(paragraphs: list[str]) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_extract_pdf_text():
    r = extract_resume_text(_pdf("Senior QA Python"), max_chars=1000)
    assert r.format == "pdf"
    assert "Senior QA Python" in r.text
    assert r.pages == 1
    assert r.truncated is False


def test_extract_docx_text_and_truncate():
    r = extract_resume_text(_docx(["Иван Петров", "Python, pytest"]), max_chars=15)
    assert r.format == "docx"
    assert r.text == "Иван Петров\nPyt"
    assert r.truncated is True


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"hello world", "только PDF и DOCX"),
        (b"PK\x03\x04broken", "повреждён"),
        (_pdf(""), "нет текстового слоя"),
    ],
)
def test_extract_rejects(data, message):
    with pytest.raises(ResumeFileError, match=message):
        extract_resume_text(data, max_chars=1000)


def test_extract_rejects_encrypted_pdf():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(_pdf("secret"))))
    writer.encrypt("pw", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ResumeFileError, match="паролем"):
        extract_resume_text(buf.getvalue(), max_chars=1000)


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
        def tailor_resume_with_fallback(self, *, resume_text, vacancy_text, match_summary):
            calls.append({"resume": resume_text, "vacancy": vacancy_text, "match": match_summary})
            return ResumeTailoringExtraction.model_validate(_ANSWER), LLMUsage(model="fallback/b")

    monkeypatch.setattr(resume_tailoring_service, "get_interactive_client", lambda: _Client())
    return calls


def test_tailor_runs_llm_then_serves_cache(api_client, fake_llm, monkeypatch, tmp_path):
    use_ai_config(monkeypatch, tmp_path, cover_letter_model="writer/a")
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)

    assert api_client.get(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token)).status_code == 404
    job = await_llm_job(api_client, token, api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token)))
    assert job["status"] == "done", job
    body = job["resume_tailoring"]
    assert body["suggestions"][0]["suggested"].startswith("Python, pytest")
    assert body["do_not_claim"] == ["Kubernetes"]
    assert (body["cached"], body["stale"]) == (False, False)
    assert fake_llm[0]["resume"] == "python, pytest"

    with session_scope() as session:
        row = session.get(ResumeTailoringRow, (rid, vid))
        assert (row.model, row.requested_model, row.prompt_version) == (
            "fallback/b",
            "writer/a",
            RESUME_TAILOR_PROMPT_VERSION,
        )

    again = api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token))
    assert again.status_code == 200
    assert again.json()["cached"] is True
    assert len(fake_llm) == 1

    forced = api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}?force=true", headers=_auth(token))
    assert await_llm_job(api_client, token, forced)["status"] == "done"
    assert len(fake_llm) == 2

    # Сменили модель писем — адаптация устарела, POST без force пересчитывает.
    use_ai_config(monkeypatch, tmp_path, cover_letter_model="writer/c")
    assert api_client.get(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token)).json()["stale"] is True
    regen = api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token))
    assert regen.status_code == 202
    # Дождаться задачи внутри теста: иначе она доработает после отката подмен — с настоящим клиентом.
    assert await_llm_job(api_client, token, regen)["status"] == "done"
    assert len(fake_llm) == 3


def test_tailor_dropped_on_resume_edit_and_delete(api_client, fake_llm):
    token = _register(api_client, "a@example.com")
    vid = _add_vacancy()
    rid = _add_resume(api_client, token)
    r = api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token))
    assert await_llm_job(api_client, token, r)["status"] == "done"

    upd = api_client.put(f"/api/v1/resumes/{rid}", headers=_auth(token), json={"text": "go, k8s"})
    assert upd.status_code == 200
    assert api_client.get(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token)).status_code == 404

    r = api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token))
    assert await_llm_job(api_client, token, r)["status"] == "done"
    assert api_client.delete(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token)).status_code == 204
    assert api_client.delete(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(token)).status_code == 404


def test_tailor_foreign_resume_and_missing_vacancy(api_client, fake_llm):
    owner = _register(api_client, "a@example.com")
    rid = _add_resume(api_client, owner)
    other = _register(api_client, "b@example.com")
    vid = _add_vacancy()
    assert api_client.post(f"/api/v1/resumes/{rid}/tailor/{vid}", headers=_auth(other)).status_code == 404
    assert api_client.post(f"/api/v1/resumes/{rid}/tailor/nope", headers=_auth(owner)).status_code == 404
    assert fake_llm == []


def test_parse_file_endpoint(api_client):
    token = _register(api_client, "a@example.com")
    url = "/api/v1/resumes/parse-file"
    r = api_client.post(url, headers=_auth(token), content=_docx(["Python developer"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "Python developer", "format": "docx", "chars": 16, "truncated": False, "pages": None}

    r = api_client.post(url, headers=_auth(token), content=b"not a resume")
    assert r.status_code == 422
    assert "PDF и DOCX" in r.json()["detail"]["message"]

    r = api_client.post(url, headers=_auth(token), content=b"%PDF-" + b"0" * (5 * 1024 * 1024))
    assert r.status_code == 413

    assert api_client.post(url, content=_docx(["x"])).status_code == 401
