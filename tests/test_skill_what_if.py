"""«Что если выучить»: недостающие технологии сегмента, новые подходящие вакансии, покрытие, премия."""

from __future__ import annotations

import pytest
from tests.test_resume_market import _add, _h, _resume, _token, _vac, fake_embed  # noqa: F401

from backend.services.skill_what_if_service import FIT_THRESHOLD, MIN_DEMAND, is_learnable


def test_is_learnable():
    assert is_learnable("Kubernetes") and is_learnable("1С") and is_learnable("Spring Boot")
    assert not is_learnable("Написание автотестов")
    assert not is_learnable("Интеграционное тестирование")


def test_what_if_options_and_peer_gaps(api_client, fake_embed):  # noqa: F811
    # Резюме знает Python и Pytest. Вакансии: 6 × [Python, Pytest, Selenium] — покрытие 2/3 < 0.7,
    # с Selenium станут подходящими; 5 × [Python, Pytest, Docker, Kafka, Helm] — 2/5, одной технологии мало (3/5).
    qa_sel = [
        _vac(
            "QA тестировщик python",
            detected_grade="middle",
            detected_specializations=["Automation QA"],
            technologies=["Python", "Pytest", "Selenium"],
            detected_salary_avg_rub=200_000,
        )
        for _ in range(6)
    ]
    qa_kafka = [
        _vac(
            "QA тестировщик python",
            detected_grade="middle",
            detected_specializations=["Automation QA"],
            technologies=["Python", "Docker", "Kafka", "Helm", "Pytest"],
        )
        for _ in range(MIN_DEMAND)
    ]
    rare = [_vac("QA тестировщик", technologies=["Python", "Pytest", "Rust"])]
    practice = [_vac("QA тестировщик", technologies=["Написание автотестов", "Python"]) for _ in range(MIN_DEMAND)]
    _add(*qa_sel, *qa_kafka, *rare, *practice)
    token = _token(api_client)
    rid = _resume(api_client, token, "QA тестировщик: Python, pytest")

    r = api_client.get(f"/api/v1/resumes/{rid}/what-if", headers=_h(token))
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["peers"] == m["with_technologies"] == 6 + MIN_DEMAND + 1 + MIN_DEMAND
    assert m["role"] == "Automation QA" and m["grade"] == "middle"
    assert m["fit_threshold"] == FIT_THRESHOLD
    # Ни одна вакансия не покрыта на 70%: 2/3, 2/5, 1/2.
    assert m["baseline_fit"] == 0
    opts = {o["technology"]: o for o in m["options"]}
    # Rust — у одной вакансии (меньше MIN_DEMAND), практика — не технология.
    assert set(opts) == {"Selenium", "Docker", "Kafka", "Helm"}
    assert m["options"][0]["technology"] == "Selenium"
    sel = opts["Selenium"]
    assert (sel["demand"], sel["new_fit"]) == (6, 6)
    assert sel["coverage_gain"] == pytest.approx(6 * (1 / 3) / m["with_technologies"])
    assert sel["median_with"] == 200_000
    assert opts["Kafka"]["new_fit"] == 0  # 3/5 — мало, нужна ещё одна
    # Сочетания UI считает по peer_gaps: Docker + Kafka вместе закрывают свои вакансии.
    gaps = m["peer_gaps"]
    assert len(gaps) == m["with_technologies"]
    both = [g for g in gaps if {"Docker", "Kafka"} <= set(g["missing"])]
    assert len(both) == MIN_DEMAND and all((g["total"], g["covered"]) == (5, 2) for g in both)
    assert all("Rust" not in g["missing"] for g in gaps)


def test_what_if_empty_and_access(api_client, fake_embed):  # noqa: F811
    token = _token(api_client)
    rid = _resume(api_client, token, "QA")
    m = api_client.get(f"/api/v1/resumes/{rid}/what-if", headers=_h(token)).json()
    assert (m["peers"], m["options"], m["peer_gaps"]) == (0, [], [])
    other = _token(api_client, "b@example.com")
    assert api_client.get(f"/api/v1/resumes/{rid}/what-if", headers=_h(other)).status_code == 404


def test_what_if_embeddings_disabled_is_503(api_client):
    token = _token(api_client)
    rid = _resume(api_client, token, "QA")
    r = api_client.get(f"/api/v1/resumes/{rid}/what-if", headers=_h(token))
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "UPSTREAM_UNAVAILABLE"
