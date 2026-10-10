"""Тесты извлечения специализаций."""

from tg_jobs_parser.config.schema import DEFAULT_SPECIALIZATIONS
from tg_jobs_parser.extraction.specialization_extractor import extract_specializations


def test_extract_ai_ml_engineer():
    text = "Senior ML Engineer, Python, remote"
    specs = extract_specializations(text, DEFAULT_SPECIALIZATIONS)
    assert "AI" in specs


def test_extract_ai_llm_prompt():
    text = "LLM / Prompt Engineer, #AI #вакансия"
    specs = extract_specializations(text, DEFAULT_SPECIALIZATIONS)
    assert "AI" in specs


def test_extract_ai_russian():
    text = "Инженер ИИ, искусственный интеллект, Москва"
    specs = extract_specializations(text, DEFAULT_SPECIALIZATIONS)
    assert "AI" in specs


def test_extract_hh_role_names():
    assert "Тестировщик" in extract_specializations("Ищем тестировщика", DEFAULT_SPECIALIZATIONS)
    assert "Программист, разработчик" in extract_specializations(
        "Вакансия: программист, удалёнка", DEFAULT_SPECIALIZATIONS
    )
