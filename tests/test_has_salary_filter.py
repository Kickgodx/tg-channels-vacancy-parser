"""Фильтр has_salary: по рублёвой сумме, как метрики и аналитика."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.db.models import VacancyRow
from backend.db.session import session_scope


def _add(vacancy_id: str, *, salary: float | None, currency: str | None, rub: float | None) -> None:
    with session_scope() as session:
        session.add(
            VacancyRow(
                id=vacancy_id,
                source_post_key=f"ch-{vacancy_id}",
                source_channel="ch",
                source_url="https://t.me/s/ch",
                post_date=datetime(2026, 9, 1, tzinfo=UTC),
                raw_text=f"Python {vacancy_id}",
                cleaned_text=f"Python {vacancy_id}",
                detected_salary_avg=salary,
                salary_currency=currency,
                detected_salary_avg_rub=rub,
            )
        )


def _ids(client, has_salary: str) -> set[str]:
    r = client.get(f"/api/v1/vacancies?has_salary={has_salary}")
    assert r.status_code == 200, r.text
    return {item["id"] for item in r.json()["items"]}


def test_has_salary_uses_rub_sum_like_metrics(admin_client):
    _add("a", salary=150_000, currency="RUB", rub=150_000)
    _add("b", salary=1_000, currency="XYZ", rub=None)  # валюта без курса
    _add("c", salary=None, currency=None, rub=None)

    assert _ids(admin_client, "true") == {"a"}
    assert _ids(admin_client, "false") == {"b", "c"}
    assert {item["id"] for item in admin_client.get("/api/v1/vacancies").json()["items"]} == {"a", "b", "c"}

    metrics = admin_client.get("/api/v1/vacancies/metrics?has_salary=true").json()
    assert metrics["total_vacancies"] == 1
    assert metrics["vacancies_with_salary"] == 1
