"""Карта рынка: расчёт (t-SNE + k-means), подписи ниш, место резюме, доступ и пересчёт."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from tests.test_resume_market import _add, _h, _resume, _token, _vac, fake_embed  # noqa: F401

from backend import maintenance
from backend.db import maintenance_requests
from backend.db.models import MaintenanceRequestRow, MarketMapPointRow, MarketMapRow
from backend.db.session import session_scope
from backend.services import market_map_service
from backend.services.market_map_service import MIN_CLUSTERS, MIN_POINTS, map_is_due, unique_labels

# Тексты дают фейковому эмбеддингу разные направления: python / golang / qa / тестиров.
_TOPICS = [
    ("python python", ["Backend"], ["Python", "Django"]),
    ("golang golang", ["Backend"], ["Go", "Kafka"]),
    ("qa qa", ["QA"], ["Postman"]),
    ("тестиров тестиров", ["Automation QA"], ["Selenium"]),
]


def _catalog(per_topic: int = 20) -> None:
    rows = []
    for text, roles, techs in _TOPICS:
        for i in range(per_topic):
            # Небольшая примесь соседней темы — точки одной темы не совпадают.
            rows.append(
                _vac(
                    f"{text} {'python ' * (i % 3)}",
                    days_ago=1 + i,
                    detected_specializations=roles,
                    technologies=techs,
                    detected_salary_avg_rub=100_000 + 1_000 * i,
                )
            )
    _add(*rows)


@pytest.fixture
def no_kick(monkeypatch):
    """Пересчёт по запросу выполняет тест сам, без фонового потока API."""
    monkeypatch.setattr(maintenance, "kick_requested_map_build", lambda: None)


def _rebuild(client, token) -> dict:
    """Кнопка «Пересчитать» и выполнение запроса фоновым обслуживанием."""
    r = client.post("/api/v1/market-map/rebuild", headers=_h(token))
    assert r.status_code == 202, r.text
    assert r.json()["rebuild_pending"] is True
    assert maintenance.run_requested_map_build() is True
    m = client.get("/api/v1/market-map", headers=_h(token)).json()
    assert m["rebuild_pending"] is False
    return m


def test_unique_labels():
    assert unique_labels([("Backend", ["Go", "Kafka"]), ("QA", [])]) == ["Backend · Go, Kafka", "QA"]
    assert unique_labels([("Backend", ["Go", "Kafka", "gRPC"]), ("Backend", ["Go", "Kafka"])]) == [
        "Backend · Go, Kafka, gRPC",
        "Backend · Go, Kafka",
    ]
    assert unique_labels([("QA", []), ("QA", [])]) == ["QA (1)", "QA (2)"]


def test_build_map_and_resume_position(api_client, fake_embed, no_kick):  # noqa: F811
    token = _token(api_client)  # первый — админ
    assert api_client.get("/api/v1/market-map", headers=_h(token)).json()["available"] is False
    _catalog()
    m = _rebuild(api_client, token)
    n = len(_TOPICS) * 20
    assert m["available"] and m["points"] == n
    assert all(len(m[col]) == n for col in ("ids", "x", "y", "cluster", "role", "grade", "salary", "company"))
    assert all(0 <= v <= 1 for v in m["x"] + m["y"])
    assert len(m["clusters"]) == MIN_CLUSTERS
    assert sum(c["size"] for c in m["clusters"]) == n
    labels = {c["label"] for c in m["clusters"]}
    assert any(lab.startswith("QA") for lab in labels) and any("Go" in lab for lab in labels)

    rid = _resume(api_client, token, "QA qa qa тестировщик")
    with_resume = api_client.get("/api/v1/market-map", params={"resume_id": rid}, headers=_h(token)).json()
    pos = with_resume["resume"]
    assert pos["resume_id"] == rid and 0 <= pos["x"] <= 1
    by_id = dict(zip(with_resume["ids"], with_resume["role"], strict=True))
    assert by_id[pos["nearest"][0]] == "QA"

    other = _token(api_client, "b@example.com")
    assert api_client.post("/api/v1/market-map/rebuild", headers=_h(other)).status_code == 403
    assert api_client.get("/api/v1/market-map", params={"resume_id": rid}, headers=_h(other)).status_code == 404
    assert api_client.get("/api/v1/market-map", headers=_h(other)).json()["points"] == n


def test_rebuild_keeps_only_latest_and_needs_enough_points(api_client, fake_embed, no_kick):  # noqa: F811
    token = _token(api_client)
    _add(*[_vac("qa") for _ in range(MIN_POINTS - 1)])
    r = api_client.post("/api/v1/market-map/rebuild", headers=_h(token))
    assert r.status_code == 422
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(MaintenanceRequestRow)) == 0

    _catalog()
    first = market_map_service.build_market_map()
    second = market_map_service.build_market_map()
    assert first is not None and second is not None and second.id != first.id
    with session_scope() as s:
        assert s.scalars(select(MarketMapRow.id)).all() == [second.id]
        assert s.scalar(select(func.count()).select_from(MarketMapPointRow)) == second.points


def test_map_is_due(db_session):
    from backend.services.embedding_service import current_model

    assert map_is_due(db_session, 24) is True
    assert map_is_due(db_session, 0) is False
    db_session.add(MarketMapRow(model=current_model(), points=0, created_at=datetime.now(UTC) - timedelta(hours=2)))
    db_session.commit()
    assert map_is_due(db_session, 24) is False
    assert map_is_due(db_session, 1) is True


def test_map_due_on_model_change(api_client, fake_embed):  # noqa: F811
    """Карта прежней модели: пересчёт, как только каталог переиндексирован; резюме на ней — ошибка."""
    from backend.services import embedding_service

    token = _token(api_client)
    rid = _resume(api_client, token, "qa qa")
    _catalog(per_topic=15)  # _add индексирует сразу
    with session_scope() as s:
        s.add(MarketMapRow(model="old/model", points=0, created_at=datetime.now(UTC) - timedelta(hours=1)))
        s.add(_vac("qa qa ещё без вектора"))
    with session_scope() as s:
        assert map_is_due(s, 24) is False  # не все вакансии с вектором текущей модели
    embedding_service.sweep_vacancies()
    with session_scope() as s:
        assert map_is_due(s, 24) is True
        assert map_is_due(s, 0) is False  # 0 — только вручную

    body = api_client.get(f"/api/v1/market-map?resume_id={rid}", headers=_h(token)).json()
    assert body["resume"] is None and "прежней моделью" in body["resume_error"]


def test_rebuild_request_waits_for_running_build_and_repeats_once(api_client, fake_embed, no_kick):  # noqa: F811
    """Запрос во время расчёта ждёт его; повторное нажатие не ставит второй пересчёт."""
    token = _token(api_client)
    _catalog(per_topic=15)
    for _ in range(2):
        assert api_client.post("/api/v1/market-map/rebuild", headers=_h(token)).status_code == 202
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(MaintenanceRequestRow)) == 1

    # Карту уже считает другой исполнитель (advisory lock занят) — запрос остаётся.
    with session_scope() as s:
        s.execute(select(func.pg_advisory_xact_lock(market_map_service._LOCK_KEY)))
        assert maintenance.run_requested_map_build() is False
    with session_scope() as s:
        assert maintenance_requests.pending_since(s, maintenance_requests.TASK_MARKET_MAP) is not None

    assert maintenance.run_requested_map_build() is True
    assert maintenance.run_requested_map_build() is False  # запрос снят, второго пересчёта нет
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(MarketMapRow)) == 1


def test_request_during_build_is_kept(pg_db, fake_embed, monkeypatch):  # noqa: F811
    """Нажатие во время расчёта не теряется: снимается только запрос, с которого расчёт начался."""
    _catalog(per_topic=15)
    with session_scope() as s:
        maintenance_requests.request_task(s, maintenance_requests.TASK_MARKET_MAP)
    real_build = market_map_service.build_market_map

    def build_and_click_again():
        built = real_build()
        with session_scope() as s:
            maintenance_requests.request_task(s, maintenance_requests.TASK_MARKET_MAP)
        return built

    monkeypatch.setattr(market_map_service, "build_market_map", build_and_click_again)
    assert maintenance.run_requested_map_build() is True
    with session_scope() as s:
        assert maintenance_requests.pending_since(s, maintenance_requests.TASK_MARKET_MAP) is not None


def test_rebuild_inprocess_runs_in_background(api_client, fake_embed):  # noqa: F811
    """Без воркера (inprocess) API сам запускает пересчёт в фоне — карта появляется без тика лидера."""
    import time

    token = _token(api_client)
    _catalog(per_topic=15)
    assert api_client.post("/api/v1/market-map/rebuild", headers=_h(token)).status_code == 202
    deadline = time.monotonic() + 60
    m = api_client.get("/api/v1/market-map", headers=_h(token)).json()
    while m["rebuild_pending"] and time.monotonic() < deadline:
        time.sleep(0.2)
        m = api_client.get("/api/v1/market-map", headers=_h(token)).json()
    assert m["rebuild_pending"] is False and m["available"] is True


def test_rebuild_worker_mode_leaves_build_to_worker(api_client, fake_embed, monkeypatch):  # noqa: F811
    from backend.settings import get_settings

    monkeypatch.setenv("MAINTENANCE_BACKEND", "worker")
    get_settings.cache_clear()
    kicked: list[int] = []
    monkeypatch.setattr(maintenance, "kick_requested_map_build", lambda: kicked.append(1))
    token = _token(api_client)
    _catalog(per_topic=15)
    assert api_client.post("/api/v1/market-map/rebuild", headers=_h(token)).status_code == 202
    assert kicked == []
    assert api_client.get("/api/v1/market-map", headers=_h(token)).json()["rebuild_pending"] is True
