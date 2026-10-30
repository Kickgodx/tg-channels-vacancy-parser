"""Технологии из открытых источников (key_skills HH, ответ LLM): без практик, фраз и склеенных списков."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobdesk.ai.merger import merge_rules_and_ai
from jobdesk.ai.schema import VacancyAIExtraction
from jobdesk.config.schema import AppConfig, clean_technology_names
from jobdesk.hh.mapper import detail_to_vacancy
from jobdesk.hh.models import VacancyDetail
from jobdesk.models.vacancy import Vacancy


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Практики, навыки и требования — не технологии.
        ("Автоматизированное тестирование", []),
        ("Английский язык", []),
        ("Тестирование", []),
        ("Коммуникабельность, ответственность", []),
        ("Знание типовых ошибок: duplicate messages, out-of-order delivery, expired offsets.", []),
        # Аббревиатуры и 1С остаются.
        ("ООП", ["ООП"]),
        ("1С:Предприятие 8.3", ["1С"]),
        # Склеенные списки делятся, версия отрезается у известной технологии.
        ("Python 3.13+, FastAPI, Pytest, SQLalchemy, Kafka.", ["Python", "FastAPI", "Pytest", "SQLalchemy", "Kafka"]),
        ("Тестирование интерфейсов на UWP / Avalonia / Qt", ["Avalonia", "Qt"]),
        ("C# или C++", ["C#", "C++"]),
        ("pytest (или unittest)", ["Pytest", "unittest"]),
        ("Spring Boot и Hibernate", ["Spring Boot", "Hibernate"]),
        ("Vue 3", ["Vue"]),
        # Число — часть названия, если без него технология неизвестна.
        ("ISO 27001", ["ISO 27001"]),
        ("OWASP Top 10", ["OWASP Top 10"]),
        # Из фразы — перечисленное в скобках и технологии каталога.
        ("Опыт работы с инструментами мониторинга (Grafana, Kibana)", ["Grafana", "Kibana"]),
        ("Навык написания автотестов на Python/Java с использованием клиентов Kafka", ["Python", "Java", "Kafka"]),
        ("Тестировании бэкенда на C++ и C#", ["C++", "C#"]),
        # Подпись категории отбрасывается.
        ("Monitoring: Sentry.", ["Sentry"]),
        ("САПР — Altium", ["Altium"]),
        # Имена технологий не ломаются.
        (".NET Framework", [".NET Framework"]),
        ("Identity and access management", ["Identity and access management"]),
        ("CI/CD", ["CI/CD"]),
        ("golang", ["Go"]),
    ],
)
def test_clean_technology_names(raw, expected):
    assert clean_technology_names([raw]) == expected


def test_clean_dedups_and_keeps_order():
    assert clean_technology_names(["Go", "golang", "Python 3.12", "python"]) == ["Go", "Python"]


def test_hh_key_skills_cleaned():
    detail = VacancyDetail.model_validate(
        {
            "id": "7",
            "name": "QA",
            "description": "<p>QA</p>",
            "alternate_url": "https://hh.ru/vacancy/7",
            "published_at": "2026-10-01T12:00:00+0300",
            "key_skills": [
                {"name": "Функциональное тестирование"},
                {"name": "Английский язык"},
                {"name": "Python 3.13+, Pytest"},
                {"name": "Postman"},
            ],
            "professional_roles": [{"id": "124", "name": "Тестировщик"}],
        }
    )
    assert detail_to_vacancy(detail).technologies == ["Python", "Pytest", "Postman"]


def test_llm_technologies_cleaned_on_merge():
    v = Vacancy(
        id="ch-1",
        source_post_key="ch-1",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        raw_text="text",
        cleaned_text="text",
        technologies=["Python"],
        parsed_at=datetime.now(UTC),
    )
    ai = VacancyAIExtraction(
        is_job_post=True,
        detected_specializations=["Backend"],
        technologies=["автоматизированное тестирование", "микросервисы", "Kafka", "PostgreSQL 16"],
        confidence=0.9,
    )
    assert merge_rules_and_ai(v, ai, AppConfig()).technologies == ["Python", "Kafka", "PostgreSQL"]
