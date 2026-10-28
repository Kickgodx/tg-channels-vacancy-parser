"""Карьерная траектория: позиция резюме, следующий грейд, соседние роли, маршрут технологий."""

from __future__ import annotations

import pytest
from tests.test_resume_market import _add, _h, _resume, _token, _vac, fake_embed  # noqa: F401

from backend.services.career_paths_service import MIN_SEGMENT, plan_route


def _peers(k: int, total: int, covered: int, missing: set[str]):
    return [(total, covered, frozenset(missing)) for _ in range(k)]


def test_route_picks_tech_that_opens_most_vacancies():
    # 6 вакансий ждут Selenium (2/3 → 3/3), 4 — Kafka (2/4 → 3/4): Selenium первым.
    peers = _peers(6, 3, 2, {"Selenium"}) + _peers(4, 4, 2, {"Kafka", "Helm"})
    route = plan_route(peers)
    assert [s.technology for s in route] == ["Selenium", "Helm"]
    assert route[0].fit_share == pytest.approx(6 / 10)
    assert route[1].fit_share == pytest.approx(1.0)
    assert route[0].demand_share == pytest.approx(6 / 10)
    # Kafka не добавляет подходящих — хвостовой шаг отброшен.
    assert route[1].coverage == pytest.approx((6 * 1 + 4 * 3 / 4) / 10)


def test_route_keeps_combo_step_and_drops_useless_tail():
    # 2/5: одной технологии мало (3/5 < 70%), двух хватает — первый шаг без новых подходящих остаётся.
    route = plan_route(_peers(5, 5, 2, {"Docker", "Kafka", "Helm"}))
    assert [s.technology for s in route] == ["Docker", "Helm"]
    assert [s.fit_share for s in route] == [0, 1.0]


def test_route_coverage_only_and_filters():
    # Ни один шаг не делает вакансию подходящей — маршрут показывает рост покрытия.
    peers = [*_peers(3, 10, 1, {"Go", "Rust"}), (10, 1, frozenset({"Написание автотестов", "Zig"}))]
    route = plan_route(peers)
    # Zig — у одной вакансии, практика — не технология.
    assert [s.technology for s in route] == ["Go", "Rust"]
    assert all(s.fit_share == 0 for s in route)
    assert plan_route([]) == []


def _vacs(k: int, text: str, **kw):
    return [_vac(text, **kw) for _ in range(k)]


def test_career_paths_grade_step_and_neighbour_role(api_client, fake_embed):  # noqa: F811
    aqa = dict(detected_specializations=["Automation QA"])
    _add(
        *_vacs(
            25,
            "QA тестировщик python",
            detected_grade="middle",
            technologies=["Python", "Pytest", "Selenium"],
            detected_salary_avg_rub=200_000,
            **aqa,
        ),
        *_vacs(
            12,
            "QA тестировщик python",
            detected_grade="senior",
            technologies=["Python", "Pytest", "Selenium", "Docker"],
            detected_salary_avg_rub=320_000,
            **aqa,
        ),
        *_vacs(
            8,
            "QA тестировщик python",
            detected_grade="senior",
            technologies=["Python", "Pytest", "Docker", "Kubernetes"],
            detected_salary_avg_rub=320_000,
            **aqa,
        ),
        *_vacs(
            20,
            "DevOps инженер python",
            detected_grade="middle",
            detected_specializations=["DevOps"],
            technologies=["Python", "Docker", "Kubernetes", "Terraform"],
            detected_salary_avg_rub=300_000,
        ),
        # Две вакансии — не соседняя роль.
        *_vacs(2, "Backend golang", detected_specializations=["Backend"], technologies=["Go"]),
    )
    token = _token(api_client)
    rid = _resume(api_client, token, "QA тестировщик: Python, pytest")

    r = api_client.get(f"/api/v1/resumes/{rid}/career-paths", headers=_h(token))
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["peers"] == 67
    cur = m["current"]
    assert (cur["role"], cur["grade"], cur["vacancies"]) == ("Automation QA", "middle", 25)
    assert cur["coverage"] == pytest.approx(2 / 3) and cur["fit_share"] == 0
    assert cur["salary_median"] == 200_000

    up, devops = m["paths"]
    assert (up["kind"], up["role"], up["grade"], up["vacancies"]) == ("grade", "Automation QA", "senior", 20)
    assert up["closeness"] is None
    assert up["coverage"] == pytest.approx(0.5)
    assert [s["technology"] for s in up["route"]] == ["Docker"]
    assert up["route"][0]["fit_share"] == pytest.approx(1.0)
    assert up["salary_uplift"] == pytest.approx(0.6)

    assert (devops["kind"], devops["role"], devops["grade"]) == ("role", "DevOps", "middle")
    assert devops["closeness"] == pytest.approx(20 / 67)
    assert devops["coverage"] == pytest.approx(0.25)
    assert [s["technology"] for s in devops["route"]] == ["Docker", "Kubernetes"]
    assert [s["fit_share"] for s in devops["route"]] == [0, 1.0]
    assert devops["salary_uplift"] == pytest.approx(0.5)


def test_career_paths_small_grade_falls_back_to_all_grades(api_client, fake_embed):  # noqa: F811
    _add(
        *_vacs(
            MIN_SEGMENT,
            "QA тестировщик python",
            detected_grade="middle",
            detected_specializations=["Automation QA"],
            technologies=["Python", "Pytest"],
        ),
        *_vacs(
            6,
            "DevOps python",
            detected_grade="middle",
            detected_specializations=["DevOps"],
            technologies=["Python", "Docker"],
        ),
        *_vacs(
            6,
            "DevOps python",
            detected_grade="senior",
            detected_specializations=["DevOps"],
            technologies=["Python", "Docker", "Kubernetes"],
        ),
    )
    token = _token(api_client)
    rid = _resume(api_client, token, "QA тестировщик: Python, pytest")
    m = api_client.get(f"/api/v1/resumes/{rid}/career-paths", headers=_h(token)).json()
    assert m["current"]["fit_share"] == 1.0
    assert m["current"]["salary_median"] is None  # зарплат нет
    # Выше middle у AQA вакансий нет — шага по грейду нет; у DevOps в middle мало — все грейды.
    (devops,) = m["paths"]
    assert (devops["kind"], devops["grade"], devops["vacancies"]) == ("role", None, 12)
    assert devops["salary_uplift"] is None


def test_career_paths_empty_and_access(api_client, fake_embed):  # noqa: F811
    token = _token(api_client)
    rid = _resume(api_client, token, "QA")
    m = api_client.get(f"/api/v1/resumes/{rid}/career-paths", headers=_h(token)).json()
    assert (m["peers"], m["current"], m["paths"]) == (0, None, [])
    other = _token(api_client, "b@example.com")
    assert api_client.get(f"/api/v1/resumes/{rid}/career-paths", headers=_h(other)).status_code == 404


def test_career_paths_embeddings_disabled_is_503(api_client):
    token = _token(api_client)
    rid = _resume(api_client, token, "QA")
    r = api_client.get(f"/api/v1/resumes/{rid}/career-paths", headers=_h(token))
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "UPSTREAM_UNAVAILABLE"
