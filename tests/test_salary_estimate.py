"""Оценка зарплаты вакансии без вилки: kNN по эмбеддингам, хранение, карточка, флаг списка."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from tests.llm_job_helpers import use_ai_config

from backend.db.models import VacancyEmbeddingRow, VacancyRow, VacancySalaryEstimateRow
from backend.db.session import session_scope
from backend.db.vector_type import EMBEDDING_DIMENSIONS
from backend.services import embedding_service
from backend.services import salary_estimate_service as est

MODEL = "test/embed"
_n = iter(range(1, 1_000_000))
_NOW = datetime.now(UTC)


@pytest.fixture
def embeddings_on(monkeypatch, tmp_path):
    use_ai_config(monkeypatch, tmp_path, embeddings={"enabled": True, "model": MODEL})
    embedding_service.reset_embedding_client()
    yield
    embedding_service.reset_embedding_client()


def _vector(angle: float, axis: int = 1) -> list[float]:
    """Единичный вектор в плоскости (0, axis): чем больше угол, тем дальше от оси 0."""
    v = [0.0] * EMBEDDING_DIMENSIONS
    v[0], v[axis] = math.cos(angle), math.sin(angle)
    return v


def _vac(
    *,
    angle: float,
    salary: float | None = None,
    grade: str = "middle",
    days_ago: int = 1,
    duplicate: bool = False,
    axis: int = 1,
    model: str = MODEL,
) -> str:
    i = next(_n)
    vid = f"s-{i}"
    with session_scope() as s:
        s.add(
            VacancyRow(
                id=vid,
                source_post_key=f"k-{i}",
                source_channel="ch",
                source_url="u",
                post_date=_NOW - timedelta(days=days_ago),
                raw_text=f"text {i}",
                cleaned_text=f"text {i}",
                detected_grade=grade,
                detected_salary_avg_rub=salary,
                is_duplicate=duplicate,
            )
        )
        s.flush()
        s.add(VacancyEmbeddingRow(vacancy_id=vid, model=model, content_hash="h", embedding=_vector(angle, axis)))
    return vid


def _estimate(vid: str) -> est.Estimate:
    with session_scope() as s:
        v = s.get(VacancyRow, vid)
        return est.estimate(s, v, s.get(VacancyEmbeddingRow, vid).embedding, model=MODEL)


def _near_peers(n: int, *, base: int = 100_000, step: int = 10_000, grade: str = "middle") -> list[str]:
    """``n`` вакансий с зарплатой рядом с осью 0: ближе всех — с меньшей зарплатой."""
    return [_vac(angle=0.01 * (i + 1), salary=base + step * i, grade=grade) for i in range(n)]


def test_estimate_nearest_same_grade(pg_db):
    target = _vac(angle=0.0)
    near = _near_peers(est.NEIGHBOURS)
    _vac(angle=1.2, salary=1_000_000)  # тот же грейд, но далеко по смыслу
    _vac(angle=0.001, salary=900_000, grade="senior")  # ближе всех, но другой грейд

    e = _estimate(target)
    assert (e.grade, e.neighbours) == ("middle", est.NEIGHBOURS)
    assert e.neighbour_ids == near
    salaries = [100_000 + 10_000 * i for i in range(est.NEIGHBOURS)]
    assert e.median == salaries[len(salaries) // 2]
    assert e.p25 == est.percentile(salaries, 0.25) and e.p75 == est.percentile(salaries, 0.75)


def test_unknown_grade_searches_all_grades(pg_db):
    target = _vac(angle=0.0, grade="unknown")
    _near_peers(est.MIN_NEIGHBOURS, grade="senior")
    e = _estimate(target)
    assert e.grade is None and e.neighbours == est.MIN_NEIGHBOURS and e.median is not None


def test_too_few_neighbours_no_estimate(pg_db):
    target = _vac(angle=0.0)
    _near_peers(est.MIN_NEIGHBOURS - 1)
    e = _estimate(target)
    assert e.neighbours == est.MIN_NEIGHBOURS - 1 and e.median is None and e.p25 is None


def test_excluded_neighbours(pg_db):
    target = _vac(angle=0.0)
    excluded = [
        _vac(angle=0.01, salary=100_000, days_ago=est.WINDOW_DAYS + 5),  # старше года до поста
        _vac(angle=0.01, salary=100_000, duplicate=True),
        _vac(angle=0.01, salary=100_000, model="other/model"),
        _vac(angle=0.01),  # без зарплаты
    ]
    e = _estimate(target)
    assert e.neighbours == 0
    assert not set(excluded) & set(e.neighbour_ids)


def test_sweep_stores_refreshes_and_drops(pg_db, embeddings_on):
    target = _vac(angle=0.0)
    with_salary = _vac(angle=0.5, salary=50_000)
    _near_peers(est.MIN_NEIGHBOURS)

    assert est.sweep_estimates(model=MODEL) == 1  # только вакансия без зарплаты
    with session_scope() as s:
        stored = s.get(VacancySalaryEstimateRow, target)
        assert stored.median is not None and s.get(VacancySalaryEstimateRow, with_salary) is None
    assert est.sweep_estimates(model=MODEL) == 0  # свежая — не пересчитывается

    # Устарела по сроку — пересчитывается.
    with session_scope() as s:
        s.execute(update(VacancySalaryEstimateRow).values(created_at=_NOW - timedelta(days=est.REFRESH_DAYS + 1)))
    assert est.sweep_estimates(model=MODEL) == 1

    # Вакансия получила зарплату — оценка удаляется.
    with session_scope() as s:
        s.execute(update(VacancyRow).where(VacancyRow.id == target).values(detected_salary_avg_rub=120_000))
    est.sweep_estimates(model=MODEL)
    with session_scope() as s:
        assert s.scalars(select(VacancySalaryEstimateRow.vacancy_id)).all() == []


def _token(client) -> dict:
    r = client.post("/api/v1/auth/register", json={"email": "a@example.com", "password": "secret1"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_endpoint_statuses(api_client, embeddings_on):
    h = _token(api_client)
    target = _vac(angle=0.0)
    near = _near_peers(est.NEIGHBOURS)
    no_vector = next(_n)
    with session_scope() as s:
        s.add(
            VacancyRow(
                id=f"s-{no_vector}",
                source_post_key="kk",
                source_channel="ch",
                source_url="u",
                raw_text="t",
                cleaned_text="t",
            )
        )

    body = api_client.get(f"/api/v1/vacancies/{target}/salary-estimate", headers=h).json()
    assert body["status"] == "ready"
    assert body["neighbours"] == est.NEIGHBOURS and body["grade"] == "middle"
    assert [x["id"] for x in body["examples"]] == near[: est.EXAMPLES]
    assert body["p25"] <= body["median"] <= body["p75"]

    assert api_client.get(f"/api/v1/vacancies/{near[0]}/salary-estimate", headers=h).json()["status"] == "has_salary"
    pending = api_client.get(f"/api/v1/vacancies/s-{no_vector}/salary-estimate", headers=h).json()
    assert pending["status"] == "pending"
    assert api_client.get("/api/v1/vacancies/nope/salary-estimate", headers=h).status_code == 404


def test_endpoint_insufficient_and_disabled(api_client, monkeypatch, tmp_path, embeddings_on):
    h = _token(api_client)
    target = _vac(angle=0.0)
    _near_peers(2)
    body = api_client.get(f"/api/v1/vacancies/{target}/salary-estimate", headers=h).json()
    assert (body["status"], body["neighbours"], body["median"]) == ("insufficient", 2, None)

    use_ai_config(monkeypatch, tmp_path, embeddings={"enabled": False})
    assert api_client.get(f"/api/v1/vacancies/{target}/salary-estimate", headers=h).json()["status"] == "unavailable"


def test_endpoint_uses_stored_fresh_estimate(api_client, embeddings_on):
    h = _token(api_client)
    target = _vac(angle=0.0)
    _near_peers(est.MIN_NEIGHBOURS)
    est.sweep_estimates(model=MODEL)
    with session_scope() as s:
        s.execute(update(VacancySalaryEstimateRow).values(median=777_000))
    body = api_client.get(f"/api/v1/vacancies/{target}/salary-estimate", headers=h).json()
    assert body["median"] == 777_000


def test_list_flag_counts_estimates_but_analytics_does_not(api_client, embeddings_on):
    h = _token(api_client)
    estimated = _vac(angle=0.0)
    no_estimate = _vac(angle=0.0, axis=2, grade="lead")  # других lead с зарплатой нет
    peers = _near_peers(est.MIN_NEIGHBOURS)
    est.sweep_estimates(model=MODEL)

    def ids(params: str) -> set[str]:
        r = api_client.get(f"/api/v1/vacancies?page_size=100&{params}", headers=h)
        assert r.status_code == 200, r.text
        return {it["id"] for it in r.json()["items"]}

    assert ids("has_salary=true") == set(peers)
    assert ids("has_salary=true&salary_estimates=true") == set(peers) | {estimated}
    assert ids("has_salary=false") == {estimated, no_estimate}
    assert ids("has_salary=false&salary_estimates=true") == {no_estimate}
    # В списке оценка не показывается: у вакансии с оценкой зарплаты нет.
    r = api_client.get("/api/v1/vacancies?has_salary=true&salary_estimates=true&page_size=100", headers=h).json()
    item = next(it for it in r["items"] if it["id"] == estimated)
    assert item["detected_salary_avg_rub"] is None

    m = api_client.get("/api/v1/vacancies/metrics?has_salary=true&salary_estimates=true", headers=h).json()
    assert m["unique_vacancies"] == len(peers)
    sal = api_client.get("/api/v1/analytics/salaries?has_salary=true&salary_estimates=true", headers=h)
    assert sal.status_code == 200


def test_evaluate_leave_one_out(pg_db):
    _near_peers(est.NEIGHBOURS + 1, step=0)  # все по 100 000 — оценка точная
    with session_scope() as s:
        r = est.evaluate(s, model=MODEL)
    assert (r.checked, r.estimated) == (est.NEIGHBOURS + 1, est.NEIGHBOURS + 1)
    assert r.median_ape == 0 and r.within_iqr == 1 and r.by_grade["middle"]["n"] == est.NEIGHBOURS + 1
