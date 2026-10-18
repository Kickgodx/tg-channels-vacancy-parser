"""Доступ к общим эндпоинтам: чтение — JWT, мутации и прогоны — admin; overrides прогона — whitelist."""

from __future__ import annotations

import uuid

import pytest

from backend.config_builder import build_config
from backend.db import parse_run_repository as pr
from backend.db.session import session_scope
from backend.schemas import RunCreateRequest
from tg_jobs_parser.config.loader import ConfigLoadError, load_config

# Фиксированный id: параметры должны совпадать у всех xdist-воркеров.
_RUN_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")

_READS = [
    "/api/v1/runs",
    f"/api/v1/runs/{_RUN_ID}",
    f"/api/v1/runs/{_RUN_ID}/vacancies",
    f"/api/v1/runs/{_RUN_ID}/events",
    f"/api/v1/runs/{_RUN_ID}/export.json",
    f"/api/v1/runs/{_RUN_ID}/export.xlsx",
    "/api/v1/vacancies",
    "/api/v1/vacancies/metrics",
    "/api/v1/vacancies/export.xlsx",
    "/api/v1/vacancies/some-id",
    "/api/v1/analytics/salaries",
    "/api/v1/analytics/sources",
    "/api/v1/analytics/companies",
    "/api/v1/fx/rates",
    "/api/v1/ai-cache/stats",
    "/api/v1/config",
    "/api/v1/config/defaults",
    "/api/v1/config/llm-models",
]

_ADMIN_MUTATIONS = [
    "/api/v1/runs",
    f"/api/v1/runs/{_RUN_ID}/cancel",
    "/api/v1/vacancies/import",
    "/api/v1/fx/refresh",
    "/api/v1/fx/recalculate-rub",
    "/api/v1/ai-cache/clear",
]

# Прогоны (вкладка «Парсинг») не-админу не отдаются даже на чтение.
_ADMIN_READS = [p for p in _READS if p.startswith("/api/v1/runs")]


def _register(client, email: str) -> dict:
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "secret1"})
    assert r.status_code == 201, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _runs_count() -> int:
    with session_scope() as session:
        return len(pr.list_parse_runs(session, limit=10))


@pytest.mark.parametrize("path", _READS)
def test_reads_require_jwt(api_client, path):
    r = api_client.get(path)
    assert r.status_code == 401, path
    assert r.json()["detail"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("path", _ADMIN_MUTATIONS)
def test_mutations_require_jwt(api_client, path):
    assert api_client.post(path, json={}).status_code == 401, path
    assert _runs_count() == 0


@pytest.mark.parametrize("path", _ADMIN_MUTATIONS)
def test_mutations_forbidden_for_non_admin(api_client, path):
    _register(api_client, "admin@example.com")
    user = _register(api_client, "user@example.com")
    assert user["user"]["is_admin"] is False

    r = api_client.post(path, json={}, headers=_auth(user["access_token"]))
    assert r.status_code == 403, path
    assert r.json()["detail"]["code"] == "FORBIDDEN"
    assert _runs_count() == 0


def test_non_admin_can_read(api_client):
    _register(api_client, "admin@example.com")
    headers = _auth(_register(api_client, "user@example.com")["access_token"])
    for path in ("/api/v1/vacancies", "/api/v1/ai-cache/stats", "/api/v1/config/defaults"):
        assert api_client.get(path, headers=headers).status_code == 200, path


@pytest.mark.parametrize("path", _ADMIN_READS)
def test_run_reads_forbidden_for_non_admin(api_client, path):
    _register(api_client, "admin@example.com")
    token = _register(api_client, "user@example.com")["access_token"]
    # 403 раньше поиска прогона — сама строка не нужна.
    r = api_client.get(path, headers=_auth(token))
    assert r.status_code == 403, path
    assert r.json()["detail"]["code"] == "FORBIDDEN"


def test_jwt_in_query_is_not_accepted_anywhere(api_client):
    """JWT в query оседает в логах прокси: ни REST, ни SSE его не принимают (SSE — ``?ticket=``)."""
    token = _register(api_client, "admin@example.com")["access_token"]
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot={}, status="completed").id

    assert api_client.get(f"/api/v1/runs/{rid}?access_token={token}").status_code == 401
    assert api_client.get(f"/api/v1/runs/{rid}/events?access_token={token}").status_code == 401

    ticket = api_client.post("/api/v1/auth/sse-ticket", headers=_auth(token)).json()["ticket"]
    with api_client.stream("GET", f"/api/v1/runs/{rid}/events?ticket={ticket}") as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")
    assert "event: done" in body


@pytest.mark.parametrize(
    "body",
    [
        {"ai": {"enabled": True, "base_url": "https://attacker.example/v1"}},
        {"ai": {"max_completion_tokens": 32000}},
        {"ai": {"concurrency": 16}},
        {"ai": {"cache_path": "/tmp/x"}},
        {"parser": {"max_posts_per_channel": 10**9}},
        {"search": {"max_days": 100000}},
        {"telegram_session_file": "/tmp/evil.session"},
        {"telegram": {"telethon_session_file": "/tmp/evil.session"}},
    ],
)
def test_run_rejects_non_whitelisted_overrides(admin_client, body):
    r = admin_client.post("/api/v1/runs", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "VALIDATION"
    assert _runs_count() == 0


def test_run_rejects_model_outside_catalog(admin_client):
    for ai in ({"model": "evil/unknown-model"}, {"fallback_models": ["evil/unknown-model"]}):
        r = admin_client.post("/api/v1/runs", json={"ai": ai})
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "VALIDATION"
        assert "evil/unknown-model" in detail["message"]
    assert _runs_count() == 0


def test_build_config_applies_whitelisted_overrides(default_test_config):
    base = load_config(default_test_config)
    assert base.llm_model_catalog, "каталог llm_models.yaml пуст"
    model_id = base.llm_model_catalog[0].id

    cfg = build_config(
        default_test_config,
        RunCreateRequest.model_validate(
            {
                "channels": ["a", "a", "b"],
                "search": {"date_from": "2026-01-01", "date_to": "2026-01-31"},
                "filters": {"countries": ["RU"], "any_specialization": True, "specializations": []},
                "parser": {"vacancy_score_threshold": 5, "vacancy_min_substantive_score": 1},
                "ai": {"enabled": True, "model": model_id, "fallback_models": [], "use_cache": False},
            }
        ),
    )

    assert cfg.sources.channels == ["a", "b"]
    assert (cfg.search.date_from, cfg.search.date_to) == ("2026-01-01", "2026-01-31")
    assert cfg.filters.countries == ["RU"]
    assert cfg.filters.any_specialization is True
    assert cfg.filters.specializations == []
    assert cfg.filters.work_formats == base.filters.work_formats  # не переданное — из config.yaml
    assert cfg.parser.vacancy_score_threshold == 5
    assert cfg.parser.vacancy_min_substantive_score == 1
    assert cfg.parser.max_posts_per_channel == base.parser.max_posts_per_channel
    assert cfg.ai.enabled is True
    assert cfg.ai.model == model_id
    assert cfg.ai.use_cache is False
    # Серверные значения override не затрагивает.
    assert cfg.ai.base_url == base.ai.base_url
    assert cfg.ai.max_completion_tokens == base.ai.max_completion_tokens
    assert cfg.telegram.telethon_session_file == base.telegram.telethon_session_file


def test_build_config_accepts_fallbacks_from_server_config(tmp_path):
    """Fallback из config.yaml (каталогом не проверяется) остаётся допустимым в override."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "ai:\n  enabled: false\n  fallback_models: [custom/server-fallback]\nfx:\n  enabled: false\n",
        encoding="utf-8",
    )
    body = RunCreateRequest.model_validate({"ai": {"fallback_models": ["custom/server-fallback"]}})
    assert build_config(cfg_path, body).ai.fallback_models == ["custom/server-fallback"]

    with pytest.raises(ConfigLoadError, match="evil/other"):
        build_config(cfg_path, RunCreateRequest.model_validate({"ai": {"model": "evil/other"}}))


_API_KEY = "test-api-key"


@pytest.fixture
def keyed_client(api_client, monkeypatch):
    """API с заданным ``API_KEY``: каждый запрос с JWT должен нести ``X-API-Key``."""
    from backend.settings import get_settings

    monkeypatch.setenv("API_KEY", _API_KEY)
    get_settings.cache_clear()
    return api_client


def _finished_import_job(user_id: int) -> str:
    from backend.db import llm_job_repository as lj

    with session_scope() as session:
        row = lj.create_llm_job(session, user_id=user_id, kind="import", params={"texts": ["x"]})
        row.status = "error"
        row.error_message = "test"
        return str(row.id)


def test_personal_endpoints_require_api_key(keyed_client):
    owner = _register(keyed_client, "owner@example.com")
    jwt = _auth(owner["access_token"])
    keyed = {**jwt, "X-API-Key": _API_KEY}
    job_id = _finished_import_job(owner["user"]["id"])
    resume = {"name": "CV", "text": "Python developer"}

    no_key = [
        keyed_client.post("/api/v1/resumes", json=resume, headers=jwt),
        keyed_client.get("/api/v1/filters", headers=jwt),
        keyed_client.get(f"/api/v1/llm-jobs/{job_id}", headers=jwt),
        keyed_client.post(f"/api/v1/llm-jobs/{job_id}/cancel", headers=jwt),
    ]
    for r in no_key:
        assert r.status_code == 401, r.request.url
        assert r.json()["detail"]["code"] == "NOT_AUTHENTICATED"
    assert keyed_client.get("/api/v1/resumes", headers=keyed).json()["items"] == []

    assert keyed_client.post("/api/v1/resumes", json=resume, headers=keyed).status_code == 201
    assert keyed_client.get("/api/v1/filters", headers=keyed).status_code == 200
    assert keyed_client.get(f"/api/v1/llm-jobs/{job_id}", headers=keyed).status_code == 200


def test_llm_job_events_by_ticket_need_no_api_key(keyed_client):
    owner = _register(keyed_client, "owner@example.com")
    job_id = _finished_import_job(owner["user"]["id"])
    headers = {**_auth(owner["access_token"]), "X-API-Key": _API_KEY}
    ticket = keyed_client.post("/api/v1/auth/sse-ticket", headers=headers).json()["ticket"]

    with keyed_client.stream("GET", f"/api/v1/llm-jobs/{job_id}/events?ticket={ticket}") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "error" in body


def test_personal_endpoints_without_api_key_setting(api_client):
    token = _register(api_client, "owner@example.com")["access_token"]
    assert api_client.get("/api/v1/filters", headers=_auth(token)).status_code == 200
