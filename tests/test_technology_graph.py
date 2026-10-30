"""Граф технологий: узлы, пары с lift, порог пары, роль узла, свой фильтр не применяется."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.db.models import VacancyRow
from backend.db.technology_graph import MIN_PAIR_COUNT, technology_graph
from backend.schemas import VacancyQuery

_ids = iter(range(1, 1_000_000))


def _row(techs: list[str], *, roles: list[str] | None = None, grade: str = "middle", duplicate=False) -> VacancyRow:
    i = next(_ids)
    return VacancyRow(
        id=f"g-{i}",
        source_post_key=f"k-{i}",
        source_channel="ch",
        source_url="u",
        post_date=datetime(2026, 10, 1, tzinfo=UTC),
        raw_text="t",
        cleaned_text="t",
        technologies=techs,
        detected_specializations=roles or ["Backend"],
        detected_grade=grade,
        is_duplicate=duplicate,
    )


@pytest.fixture
def corpus(db_session):
    rows = (
        [_row(["Kafka", "Go"]) for _ in range(4)]
        + [_row(["Python", "SQL"], roles=["QA"]) for _ in range(3)]
        + [_row(["Python"], roles=["AI"]) for _ in range(2)]
        + [_row(["Go"]) for _ in range(2)]
        + [_row(["Kafka", "Python"]) for _ in range(MIN_PAIR_COUNT - 1)]  # пара реже порога
        + [_row(["Kafka", "Go"], duplicate=True) for _ in range(5)]  # дубли не считаются
        + [_row([]) for _ in range(9)]
    )
    db_session.add_all(rows)
    db_session.commit()
    return rows


def test_nodes_edges_and_lift(db_session, corpus):
    g = technology_graph(db_session, VacancyQuery())
    total = 4 + 3 + 2 + 2 + (MIN_PAIR_COUNT - 1) + 9
    assert g.total == total
    nodes = {n.name: n for n in g.nodes}
    assert nodes["Python"].count == 3 + 2 + MIN_PAIR_COUNT - 1
    assert nodes["Go"].count == 6
    assert nodes["Kafka"].count == 4 + MIN_PAIR_COUNT - 1
    assert nodes["Go"].share == pytest.approx(6 / total)

    edges = {(e.source, e.target): e for e in g.edges}
    assert ("Kafka", "Python") not in edges  # реже порога
    go_kafka = edges[("Go", "Kafka")]
    assert go_kafka.count == 4
    assert go_kafka.lift == pytest.approx(4 * total / (6 * nodes["Kafka"].count))
    assert edges[("Python", "SQL")].count == 3
    assert [(e.source, e.target) for e in g.edges] == sorted(edges, key=lambda k: -edges[k].lift)


def test_node_role(db_session, corpus):
    nodes = {n.name: n for n in technology_graph(db_session, VacancyQuery()).nodes}
    assert nodes["SQL"].role == "QA" and nodes["SQL"].role_share == 1.0
    assert nodes["Go"].role == "Backend"


def test_own_technology_filter_ignored_other_filters_apply(db_session, corpus):
    by_tech = technology_graph(db_session, VacancyQuery(technologies=["Kafka"]))
    assert {n.name for n in by_tech.nodes} == {"Kafka", "Go", "Python", "SQL"}
    qa = technology_graph(db_session, VacancyQuery(specializations=["QA"]))
    assert {n.name for n in qa.nodes} == {"Python", "SQL"} and qa.total == 3


def test_node_limit_and_empty(db_session, corpus):
    g = technology_graph(db_session, VacancyQuery(), nodes=2)
    assert [n.name for n in g.nodes] == ["Python", "Go"]
    assert all({e.source, e.target} <= {"Python", "Go"} for e in g.edges)
    empty = technology_graph(db_session, VacancyQuery(grade="lead"))
    assert (empty.total, empty.nodes, empty.edges) == (0, [], [])


def test_endpoint(api_client, corpus):
    r = api_client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "secret1"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    body = api_client.get("/api/v1/analytics/technology-graph?nodes=10", headers=h).json()
    assert body["min_pair_count"] == MIN_PAIR_COUNT and len(body["nodes"]) == 4
    assert api_client.get("/api/v1/analytics/technology-graph?nodes=500", headers=h).status_code == 422
    assert api_client.get("/api/v1/analytics/technology-graph").status_code == 401
