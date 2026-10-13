"""Маппинг HH detail/short → Vacancy."""

from __future__ import annotations

from tg_jobs_parser.hh.grades import grade_from_experience, grade_from_experience_name
from tg_jobs_parser.hh.mapper import (
    clean_hh_url,
    detail_to_vacancy,
    html_to_text,
    map_work_format,
    short_to_vacancy,
    stable_vacancy_id,
)
from tg_jobs_parser.hh.models import IdName, VacancyDetail, VacancyShort


def test_grade_from_experience_ids():
    assert grade_from_experience(IdName(id="noExperience", name="Нет опыта")) == "intern"
    assert grade_from_experience(IdName(id="between1And3", name="От 1 года до 3 лет")) == "junior"
    assert grade_from_experience(IdName(id="between3And6", name="От 3 до 6 лет")) == "middle"
    assert grade_from_experience(IdName(id="moreThan6", name="Более 6 лет")) == "senior+"
    assert grade_from_experience(None) == "unknown"
    assert grade_from_experience(IdName(id="weird")) == "unknown"


def test_grade_from_experience_name_backfill():
    assert grade_from_experience_name("Нет опыта") == "intern"
    assert grade_from_experience_name("От 1 года до 3 лет") == "junior"
    assert grade_from_experience_name("От 3 до 6 лет") == "middle"
    assert grade_from_experience_name("Более 6 лет") == "senior+"
    assert grade_from_experience_name("1–3 года") == "junior"
    assert grade_from_experience_name(None) == "unknown"
    assert grade_from_experience_name("что-то ещё") == "unknown"


def test_html_to_text_strips_tags():
    assert "Hello world" in html_to_text("<p>Hello <b>world</b></p>")


def test_map_work_format_priority():
    items = [IdName(id="ON_SITE"), IdName(id="REMOTE"), IdName(id="HYBRID")]
    assert map_work_format(items) == "remote"


def test_clean_hh_url_strips_tech_source():
    assert clean_hh_url("https://hh.ru/vacancy/42?tech_source=api") == "https://hh.ru/vacancy/42"
    assert clean_hh_url("https://hh.ru/vacancy/42?tech_source=api&foo=1") == "https://hh.ru/vacancy/42?foo=1"
    assert clean_hh_url("https://hh.ru/vacancy/42") == "https://hh.ru/vacancy/42"


def test_detail_to_vacancy_fields():
    detail = VacancyDetail.model_validate(
        {
            "id": "42",
            "name": "AQA Python",
            "description": "<p>Нужен pytest</p>",
            "alternate_url": "https://hh.ru/vacancy/42?tech_source=api",
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
    assert v.post_url == "https://hh.ru/vacancy/42"
    assert "pytest" in v.cleaned_text.lower() or "AQA" in v.cleaned_text
    assert "Тестировщик" in v.detected_specializations
    assert "QA" in v.detected_specializations
    assert v.detected_grade == "junior"
    assert v.required_experience == "1–3 года"


def test_detail_to_vacancy_all_experience_grades():
    cases = (
        ("noExperience", "Нет опыта", "intern"),
        ("between1And3", "От 1 года до 3 лет", "junior"),
        ("between3And6", "От 3 до 6 лет", "middle"),
        ("moreThan6", "Более 6 лет", "senior+"),
    )
    for exp_id, exp_name, grade in cases:
        detail = VacancyDetail.model_validate(
            {
                "id": exp_id,
                "name": "Dev",
                "description": "<p>x</p>",
                "alternate_url": f"https://hh.ru/vacancy/{exp_id}",
                "published_at": "2026-10-01T12:00:00+0300",
                "experience": {"id": exp_id, "name": exp_name},
            }
        )
        v = detail_to_vacancy(detail)
        assert v.detected_grade == grade
        assert v.required_experience == exp_name


def test_short_to_vacancy_experience_grade():
    short = VacancyShort.model_validate(
        {
            "id": "8",
            "name": "SDET",
            "alternate_url": "https://hh.ru/vacancy/8",
            "experience": {"id": "moreThan6", "name": "Более 6 лет"},
        }
    )
    assert short_to_vacancy(short).detected_grade == "senior+"


def test_detail_to_vacancy_developer_role_dual_tag():
    detail = VacancyDetail.model_validate(
        {
            "id": "96",
            "name": "Python developer",
            "description": "<p>Backend</p>",
            "alternate_url": "https://hh.ru/vacancy/96",
            "published_at": "2026-10-01T12:00:00+0300",
            "professional_roles": [{"id": "96", "name": "Программист, разработчик"}],
        }
    )
    v = detail_to_vacancy(detail)
    assert "Программист, разработчик" in v.detected_specializations
    assert "Software Engineer" in v.detected_specializations


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
