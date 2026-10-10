"""Маппинг HH detail/short → Vacancy."""

from __future__ import annotations

from tg_jobs_parser.hh.mapper import (
    detail_to_vacancy,
    html_to_text,
    map_work_format,
    short_to_vacancy,
    stable_vacancy_id,
)
from tg_jobs_parser.hh.models import IdName, VacancyDetail, VacancyShort


def test_html_to_text_strips_tags():
    assert "Hello world" in html_to_text("<p>Hello <b>world</b></p>")


def test_map_work_format_priority():
    items = [IdName(id="ON_SITE"), IdName(id="REMOTE"), IdName(id="HYBRID")]
    assert map_work_format(items) == "remote"


def test_detail_to_vacancy_fields():
    detail = VacancyDetail.model_validate(
        {
            "id": "42",
            "name": "AQA Python",
            "description": "<p>Нужен pytest</p>",
            "alternate_url": "https://hh.ru/vacancy/42",
            "published_at": "2026-10-01T12:00:00+0300",
            "area": {"id": "1", "name": "Москва"},
            "employer": {"id": "9", "name": "ACME"},
            "salary_range": {"from": 200000, "to": 300000, "currency": "RUR"},
            "work_format": [{"id": "REMOTE"}],
            "experience": {"id": "between1And3", "name": "1–3 года"},
            "employment_form": {"id": "FULL", "name": "Полная"},
            "key_skills": [{"name": "pytest"}, {"name": "Python"}],
            "professional_roles": [{"id": "124", "name": "Тестировщик"}],
        }
    )
    v = detail_to_vacancy(detail)
    assert v.id == stable_vacancy_id("42")
    assert v.source_channel == "hh"
    assert v.source_post_key == "hh-42"
    assert v.company == "ACME"
    assert v.city == "Москва"
    assert v.salary_currency == "RUB"
    assert v.detected_salary_min == 200000
    assert v.work_format == "remote"
    assert "pytest" in v.technologies
    assert v.extraction_source == "hh_api"
    assert "pytest" in v.cleaned_text.lower() or "AQA" in v.cleaned_text


def test_short_to_vacancy_duplicate_flags():
    short = VacancyShort.model_validate(
        {
            "id": "7",
            "name": "SDET",
            "alternate_url": "https://hh.ru/vacancy/7",
            "area": {"name": "Казань"},
            "employer": {"name": "ACME"},
        }
    )
    v = short_to_vacancy(
        short,
        is_duplicate=True,
        duplicate_group_id=stable_vacancy_id("1"),
        duplicate_reason="hh_multicity",
    )
    assert v.is_duplicate is True
    assert v.duplicate_reason == "hh_multicity"
