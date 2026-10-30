"""Тесты извлечения специализаций."""

from jobdesk.config.schema import DEFAULT_SPECIALIZATIONS
from jobdesk.extraction.specialization_extractor import extract_specializations


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


def test_qa_synonyms_collapse_into_canonical_roles():
    # Тестировщик / Manual QA — это QA, AQA — Automation QA, роль HH «Программист, разработчик» —
    # Software Engineer; отдельных тегов нет.
    for old in ("Тестировщик", "Manual QA", "AQA", "Программист, разработчик"):
        assert old not in DEFAULT_SPECIALIZATIONS
    assert extract_specializations("Ищем тестировщика", DEFAULT_SPECIALIZATIONS) == ["QA"]
    assert extract_specializations("Нужен Manual QA в команду", DEFAULT_SPECIALIZATIONS) == ["QA"]
    assert extract_specializations("Ищем AQA на проект", DEFAULT_SPECIALIZATIONS) == ["Automation QA"]
    assert "Software Engineer" in extract_specializations("Программист, разработчик; удалёнка", DEFAULT_SPECIALIZATIONS)
    assert extract_specializations("Вакансия: программист, удалёнка", DEFAULT_SPECIALIZATIONS) == ["Software Engineer"]
