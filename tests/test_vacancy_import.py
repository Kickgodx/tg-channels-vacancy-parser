"""Ручной импорт вакансий текстом: rules-разбор, дедуп по хэшу, LLM-ветка, auth."""

from __future__ import annotations

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


def test_import_with_ai_enrichment(api_client, monkeypatch, default_test_config):
    """ai.enabled → текст идёт через AIEnrichmentService; None от LLM = «не вакансия»."""
    from tg_jobs_parser.ai.enricher import RunAIStats
    from tg_jobs_parser.config.loader import load_config

    real_cfg = load_config(default_test_config)
    ai_cfg = real_cfg.model_copy(update={"ai": real_cfg.ai.model_copy(update={"enabled": True})})
    monkeypatch.setattr(import_service, "load_config", lambda *a, **k: ai_cfg)

    llm_calls: list[str] = []

    class _FakeAI:
        def __init__(self, config):
            self.stats = RunAIStats()

        def begin_batch(self, total):
            pass

        def enrich(self, vacancy, *, cancel=None):
            self.stats.calls += 1
            llm_calls.append(vacancy.id)
            if "пикник" in vacancy.cleaned_text:
                self.stats.dropped_not_job += 1
                return None
            return vacancy.model_copy(update={"extraction_source": "hybrid", "ai_confidence": 0.9})

        def log_batch_finished(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("tg_jobs_parser.ai.enricher.AIEnrichmentService", _FakeAI, raising=True)

    token = _register(api_client, "imp@example.com")
    r = _import(api_client, token, [VACANCY_TEXT, NOT_VACANCY_TEXT])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ai_enabled"] is True
    created, rejected = body["items"]
    assert created["status"] == "created"
    assert created["vacancy"]["extraction_source"] == "hybrid"
    assert rejected["status"] == "not_vacancy"
    assert "не похож" in rejected["message"]
    # score-гейт перед LLM отключён: оба текста дошли до модели
    assert len(llm_calls) == 2
