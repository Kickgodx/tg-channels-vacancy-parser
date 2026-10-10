"""Дедуп HH: порт кейсов из hhparser."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tg_jobs_parser.hh.dedup import dedupe_vacancies, normalize_text, vacancy_key
from tg_jobs_parser.hh.models import VacancyShort


def _vacancy_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "1",
        "name": "QA Automation Engineer",
        "alternate_url": "https://hh.ru/vacancy/1",
        "area": {"id": "1", "name": "Москва"},
        "employer": {"id": "100", "name": "ACME"},
        "snippet": {
            "requirement": "Опыт <highlighttext>Python</highlighttext>",
            "responsibility": "Писать автотесты",
        },
        "work_format": [{"id": "REMOTE", "name": "Удалённо"}],
        "salary_range": {
            "from": 100000,
            "to": 200000,
            "currency": "RUR",
            "gross": False,
        },
        "experience": {"id": "between1And3", "name": "От 1 года до 3 лет"},
    }
    data.update(overrides)
    return data


@pytest.fixture
def make_vacancy() -> Callable[..., VacancyShort]:
    def factory(**overrides: Any) -> VacancyShort:
        return VacancyShort.model_validate(_vacancy_dict(**overrides))

    return factory


def test_normalize_text_strips_highlight_case_and_spaces():
    raw = "  Опыт <highlighttext>Python</highlighttext>\n  от 3 лет "
    assert normalize_text(raw) == "опыт python от 3 лет"


def test_same_vacancy_in_other_city_has_same_key(make_vacancy):
    moscow = make_vacancy(id="1", area={"id": "1", "name": "Москва"})
    spb = make_vacancy(
        id="2",
        area={"id": "2", "name": "Санкт-Петербург"},
        published_at="2026-10-01T10:00:00+0300",
        alternate_url="https://hh.ru/vacancy/2",
    )
    assert vacancy_key(moscow) == vacancy_key(spb)


def test_dedupe_keeps_first_and_records_copies(make_vacancy):
    items = [
        make_vacancy(id="1", area={"name": "Москва"}),
        make_vacancy(id="2", area={"name": "Казань"}),
        make_vacancy(id="3", name="Other role", area={"name": "Москва"}),
    ]
    groups = dedupe_vacancies(items)
    assert len(groups) == 2
    first = next(g for g in groups if g.id == "1")
    assert first.duplicates == [("2", "Казань")]
