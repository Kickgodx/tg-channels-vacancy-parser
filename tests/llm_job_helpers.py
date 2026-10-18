"""Хелперы для тестов фоновых LLM-джоб (202 → poll)."""

from __future__ import annotations

import time
from typing import Any


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def await_llm_job(client, token: str, post_response: Any, *, timeout: float = 5.0) -> dict:
    """Дождаться done/error/cancelled по 202; при 200 вернуть тело ответа как «готовый» результат."""
    if post_response.status_code == 200:
        return {"status": "done", "body": post_response.json(), "cached_response": True}
    assert post_response.status_code == 202, post_response.text
    job_id = post_response.json()["job_id"]
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/llm-jobs/{job_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("done", "error", "cancelled"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"LLM job {job_id} не завершилась: {last}")


_MODEL_FIELDS = ("model", "match_model", "cover_letter_model")


def use_ai_config(monkeypatch, tmp_path, **ai: Any):
    """Подменить ``config.yaml`` тестов: базовый конфиг + поля ``ai`` (модели и т. п.).

    Модели конфигурации обязаны быть в каталоге, поэтому рядом кладётся ``llm_models.yaml``
    проекта, дополненный id моделей из ``ai`` (например ``match/a``).
    """
    import yaml
    from tests.conftest import _TEST_CONFIG

    from backend.settings import get_settings
    from tg_jobs_parser.ai.llm_models_registry import default_llm_models_path
    from tg_jobs_parser.config.loader import load_config

    raw = yaml.safe_load(_TEST_CONFIG)
    raw["ai"].update(ai)
    catalog = yaml.safe_load(default_llm_models_path().read_text(encoding="utf-8"))
    known = {m["id"] for m in catalog["models"]}
    wanted = [ai.get(f) for f in _MODEL_FIELDS] + list(ai.get("fallback_models") or [])
    for model_id in dict.fromkeys(m for m in wanted if m and m not in known):
        catalog["models"].append({"id": model_id, "name": model_id, "cost": "0.1/0.5"})
    (tmp_path / "llm_models.yaml").write_text(yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8")
    path = tmp_path / f"config_{len(list(tmp_path.glob('config_*.yaml')))}.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(path))
    get_settings.cache_clear()
    return load_config(path).ai


def current_ai():
    """``AiConfig`` текущего тестового конфига (как его видят роуты и воркер)."""
    from backend.settings import get_settings
    from tg_jobs_parser.config.loader import load_config

    return load_config(get_settings().config_path).ai
