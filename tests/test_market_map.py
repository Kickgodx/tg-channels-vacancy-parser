"""Карта рынка: расчёт (t-SNE + k-means), подписи ниш, место резюме, доступ и пересчёт."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from tests.test_resume_market import _add, _h, _resume, _token, _vac, fake_embed  # noqa: F401

from backend.db.models import MarketMapPointRow, MarketMapRow
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


def test_unique_labels():
    assert unique_labels([("Backend", ["Go", "Kafka"]), ("QA", [])]) == ["Backend · Go, Kafka", "QA"]
    assert unique_labels([("Backend", ["Go", "Kafka", "gRPC"]), ("Backend", ["Go", "Kafka"])]) == [
        "Backend · Go, Kafka, gRPC",
        "Backend · Go, Kafka",
    ]
    assert unique_labels([("QA", []), ("QA", [])]) == ["QA (1)", "QA (2)"]


def test_build_map_and_resume_position(api_client, fake_embed):  # noqa: F811
    token = _token(api_client)  # первый — админ
    assert api_client.get("/api/v1/market-map", headers=_h(token)).json()["available"] is False
    _catalog()
    r = api_client.post("/api/v1/market-map/rebuild", headers=_h(token))
    assert r.status_code == 200, r.text
    m = r.json()
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


def test_rebuild_keeps_only_latest_and_needs_enough_points(api_client, fake_embed):  # noqa: F811
    token = _token(api_client)
    _add(*[_vac("qa") for _ in range(MIN_POINTS - 1)])
    r = api_client.post("/api/v1/market-map/rebuild", headers=_h(token))
    assert r.status_code == 422

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
