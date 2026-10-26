"""Эмбеддинги: индексация, похожие вакансии, смысловой поиск, подбор под резюме, пакетная оценка."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from tests.llm_job_helpers import await_llm_job, use_ai_config

from backend.db.models import VacancyEmbeddingRow, VacancyMatchRow, VacancyRow
from backend.db.session import session_scope
from backend.db.vector_type import EMBEDDING_DIMENSIONS, parse_vector, vector_literal
from backend.services import embedding_service, match_service
from backend.services.embedding_service import EmbeddingsUnavailable

_TOPICS = ["python", "golang", "frontend", "react", "qa", "тестиров", "devops", "kubernetes"]


def _fake_vector(text: str) -> list[float]:
    """Вектор по темам текста: у похожих по теме текстов — близкие направления."""
    t = text.lower()
    v = [0.0] * EMBEDDING_DIMENSIONS
    for i, topic in enumerate(_TOPICS):
        v[i] = float(t.count(topic))
    v[len(_TOPICS)] = 0.05  # общий компонент: нет нулевых векторов
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


@pytest.fixture
def fake_embed(monkeypatch, tmp_path):
    """Эмбеддинги включены, провайдер — детерминированная функция; считаем вызовы и тексты."""
    use_ai_config(monkeypatch, tmp_path, embeddings={"enabled": True, "batch_size": 2})
    embedding_service.reset_embedding_client()
    calls: list[list[str]] = []

    def _embed(texts, *, ai=None):
        calls.append(list(texts))
        return [_fake_vector(t) for t in texts]

    monkeypatch.setattr(embedding_service, "embed_texts", _embed)
    yield calls
    embedding_service.reset_embedding_client()


_n = iter(range(1, 1_000_000))
_NOW = datetime(2026, 10, 20, tzinfo=UTC)


def _vac(text: str, *, archived=False, duplicate=False, days_ago=0, **kw) -> VacancyRow:
    i = next(_n)
    return VacancyRow(
        id=f"e-{i}",
        source_post_key=f"k-{i}",
        source_channel="ch",
        source_url="u",
        post_date=_NOW - timedelta(days=days_ago),
        raw_text=text,
        cleaned_text=text,
        archived_at=_NOW if archived else None,
        is_duplicate=duplicate,
        **kw,
    )


def _add(*rows) -> list[str]:
    ids = [r.id for r in rows]
    with session_scope() as s:
        s.add_all(rows)
    return ids


def _embedded_ids() -> set[str]:
    with session_scope() as s:
        return set(s.scalars(select(VacancyEmbeddingRow.vacancy_id)))


# --- тип vector ---------------------------------------------------------------------------


def test_vector_literal_roundtrip():
    assert parse_vector(vector_literal([0.5, -1.0, 1e-9])) == [0.5, -1.0, 1e-9]
    assert parse_vector(None) is None


# --- индексация ---------------------------------------------------------------------------


def test_sweep_indexes_unique_vacancies_in_batches(pg_db, fake_embed):
    a, b, c = _add(_vac("Python backend"), _vac("Golang"), _vac("QA тестировщик", archived=True))
    _add(_vac("Python дубль", duplicate=True))

    r = embedding_service.sweep_vacancies()
    assert (r.embedded, r.unchanged, r.failed) == (3, 0, False)
    assert _embedded_ids() == {a, b, c}
    assert [len(batch) for batch in fake_embed] == [2, 1]  # batch_size 2, активные первыми
    assert "Python backend" in fake_embed[0][0]

    # Ничего не менялось — к API не ходим.
    assert embedding_service.sweep_vacancies().embedded == 0
    assert len(fake_embed) == 2


def test_sweep_reembeds_changed_text_and_touches_unchanged(pg_db, fake_embed):
    a, b = _add(_vac("Python"), _vac("Golang"))
    embedding_service.sweep_vacancies()
    later = datetime.now(UTC) + timedelta(minutes=1)
    with session_scope() as s:
        s.execute(
            update(VacancyRow).where(VacancyRow.id == a).values(cleaned_text="Kubernetes devops", updated_at=later)
        )
        s.execute(update(VacancyRow).where(VacancyRow.id == b).values(updated_at=later))
    fake_embed.clear()

    r = embedding_service.sweep_vacancies()
    assert (r.embedded, r.unchanged) == (1, 1)
    assert len(fake_embed) == 1 and len(fake_embed[0]) == 1
    assert "Kubernetes devops" in fake_embed[0][0]


def test_sweep_reembeds_on_model_change(pg_db, fake_embed, monkeypatch, tmp_path):
    _add(_vac("Python"))
    embedding_service.sweep_vacancies()
    use_ai_config(monkeypatch, tmp_path, embeddings={"enabled": True, "model": "openai/text-embedding-3-large"})
    assert embedding_service.sweep_vacancies().embedded == 1
    with session_scope() as s:
        assert s.scalar(select(VacancyEmbeddingRow.model)) == "openai/text-embedding-3-large"


def test_sweep_stops_on_provider_error(pg_db, fake_embed, monkeypatch):
    _add(_vac("Python"), _vac("Golang"), _vac("QA"))

    def _boom(texts, *, ai=None):
        raise EmbeddingsUnavailable("down")

    monkeypatch.setattr(embedding_service, "embed_texts", _boom)
    r = embedding_service.sweep_vacancies()
    assert (r.embedded, r.failed) == (0, True)
    assert _embedded_ids() == set()


def test_sweep_disabled(pg_db, monkeypatch, tmp_path):
    use_ai_config(monkeypatch, tmp_path, embeddings={"enabled": False})
    _add(_vac("Python"))
    assert embedding_service.sweep_vacancies().embedded == 0


# --- API ------------------------------------------------------------------------------------


def _token(client, email="a@example.com") -> str:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    return r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_similar_vacancies(api_client, fake_embed):
    token = _token(api_client)
    target, py2, go, old_py, near = _add(
        _vac("Python backend разработчик"),
        _vac("Python разработчик, немного QA"),
        _vac("Golang"),
        _vac("Python backend", archived=True),
        _vac("Python backend разработчик!"),
    )
    pending = api_client.get(f"/api/v1/vacancies/{target}/similar", headers=_h(token)).json()
    assert pending == {"items": [], "pending": True}

    embedding_service.sweep_vacancies()
    body = api_client.get(f"/api/v1/vacancies/{target}/similar?limit=5", headers=_h(token)).json()
    ids = [it["vacancy"]["id"] for it in body["items"]]
    # Тот же текст (near) и архив не показываются; Python ближе, чем Go.
    assert near not in ids and old_py not in ids and target not in ids
    assert ids[0] == py2 and go in ids
    assert body["items"][0]["similarity"] > body["items"][-1]["similarity"]
    assert api_client.get("/api/v1/vacancies/nope/similar", headers=_h(token)).status_code == 404


def test_similar_limits_one_company(pg_db, fake_embed):
    target, *same = _add(
        _vac("Python backend"),
        *[_vac(f"Python backend {'qa ' * i}", company="Centicore", company_key="centicore") for i in range(1, 5)],
    )
    (other,) = _add(_vac("Python backend golang", company="Other", company_key="other"))
    embedding_service.sweep_vacancies()
    with session_scope() as s:
        found = embedding_service.similar_vacancies(s, s.get(VacancyRow, target), limit=5)
    ids = [vid for vid, _ in found]
    assert sum(1 for vid in ids if vid in same) == 2
    assert other in ids


def test_semantic_search_list_and_analytics(api_client, fake_embed):
    token = _token(api_client)
    py, go, qa = _add(_vac("Python backend"), _vac("Golang", detected_grade="senior"), _vac("QA тестировщик"))
    embedding_service.sweep_vacancies()

    r = api_client.get("/api/v1/vacancies?q=golang&search_mode=semantic", headers=_h(token))
    assert r.status_code == 200, r.text
    ids = [it["id"] for it in r.json()["items"]]
    assert ids[0] == go and set(ids) == {py, go, qa}  # окно 300 — все три, ближайшая первая

    # Остальные фильтры — внутри окна.
    r = api_client.get("/api/v1/vacancies?q=python&search_mode=semantic&grade=senior", headers=_h(token))
    assert [it["id"] for it in r.json()["items"]] == [go]

    # Полнотекстовый режим по-прежнему ищет по словам.
    r = api_client.get("/api/v1/vacancies?q=golang", headers=_h(token))
    assert [it["id"] for it in r.json()["items"]] == [go]

    assert (
        api_client.get("/api/v1/analytics/salaries?q=golang&search_mode=semantic", headers=_h(token)).status_code == 200
    )
    status = api_client.get("/api/v1/vacancies/semantic-status", headers=_h(token)).json()
    assert (status["vacancies"], status["embedded"], status["enabled"]) == (3, 3, True)


def test_semantic_search_unavailable_is_503(api_client, monkeypatch, tmp_path):
    use_ai_config(monkeypatch, tmp_path, embeddings={"enabled": False})
    embedding_service.reset_embedding_client()
    token = _token(api_client)
    r = api_client.get("/api/v1/vacancies?q=python&search_mode=semantic", headers=_h(token))
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_resume_fit_sort(api_client, fake_embed):
    token = _token(api_client)
    rid = api_client.post("/api/v1/resumes", headers=_h(token), json={"name": "CV", "text": "QA тестировщик"}).json()[
        "id"
    ]
    py, qa = _add(_vac("Python backend", days_ago=0), _vac("QA тестировщик ручное", days_ago=5))
    embedding_service.sweep_vacancies()
    (fresh,) = _add(_vac("QA без вектора", days_ago=0))  # ещё не проиндексирована — в конце
    fake_embed.clear()

    url = f"/api/v1/vacancies?resume_id={rid}&sort_by=resume_fit"
    ids = [it["id"] for it in api_client.get(url, headers=_h(token)).json()["items"]]
    assert ids == [qa, py, fresh]
    assert fake_embed == [["QA тестировщик"]]  # вектор резюме посчитан один раз

    api_client.get(url, headers=_h(token))
    assert len(fake_embed) == 1  # из БД

    api_client.put(f"/api/v1/resumes/{rid}", headers=_h(token), json={"text": "Python backend"})
    ids = [it["id"] for it in api_client.get(url, headers=_h(token)).json()["items"]]
    assert ids[0] == py and len(fake_embed) == 2

    assert api_client.get("/api/v1/vacancies?sort_by=resume_fit", headers=_h(token)).status_code == 422


def test_match_batch_scores_skips_fresh_and_reports(api_client, monkeypatch):
    from tests.llm_job_helpers import current_ai

    from backend.db import repository as repo
    from tg_jobs_parser.ai.prompts import MATCH_PROMPT_VERSION

    token = _token(api_client)
    rid = api_client.post("/api/v1/resumes", headers=_h(token), json={"name": "CV", "text": "python"}).json()["id"]
    a, b, c = _add(_vac("Python"), _vac("Golang"), _vac("QA"))

    def _upsert(vid: str, score: int):
        with session_scope() as s:
            return repo.upsert_vacancy_match(
                s,
                resume_id=rid,
                vacancy_id=vid,
                overall_score=score,
                confidence=80,
                breakdown={},
                explanation="",
                matching_skills=[],
                missing_skills=[],
                strengths=[],
                risks=[],
                model="m",
                requested_model=current_ai().resolved_match_model(),
                prompt_version=MATCH_PROMPT_VERSION,
            )

    _upsert(b, 55)  # свежая оценка — пропуск
    called: list[str] = []

    def _fake(*, resume_id, resume_text, vacancy_id, vacancy_text, is_cancelled=None):
        called.append(vacancy_id)
        if vacancy_id == c:
            raise RuntimeError("provider down")
        return _upsert(vacancy_id, 77)

    monkeypatch.setattr(match_service, "match_single", _fake)
    r = api_client.post(f"/api/v1/resumes/{rid}/match-batch", headers=_h(token), json={"vacancy_ids": [a, b, c, a]})
    assert r.status_code == 202, r.text
    job = await_llm_job(api_client, token, r)
    assert job["status"] == "done", job
    res = job["match_batch_result"]
    assert (res["total"], res["scored"], res["skipped"], res["errors"]) == (3, 1, 1, 1)
    assert [(it["vacancy_id"], it["status"], it["score"]) for it in res["items"]] == [
        (a, "scored", 77),
        (b, "skipped", 55),
        (c, "error", None),
    ]
    assert called == [a, c]
    with session_scope() as s:
        assert s.get(VacancyMatchRow, (rid, a)).overall_score == 77

    other = _token(api_client, "b@example.com")
    assert (
        api_client.post(f"/api/v1/resumes/{rid}/match-batch", headers=_h(other), json={"vacancy_ids": [a]}).status_code
        == 404
    )
    too_many = api_client.post(
        f"/api/v1/resumes/{rid}/match-batch", headers=_h(token), json={"vacancy_ids": [f"x{i}" for i in range(21)]}
    )
    assert too_many.status_code == 422
