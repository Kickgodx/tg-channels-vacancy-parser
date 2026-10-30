"""Нормализация грейда (парсинг сохраняет все грейды; фильтр — только в API вакансий)."""

from jobdesk.extraction.grade_extractor import GRADE_OPTIONS, normalize_grade


def test_normalize_grade_aliases():
    assert normalize_grade("Senior") == "senior"
    assert normalize_grade("jr") == "junior"
    assert normalize_grade("junior+") == "junior+"
    assert normalize_grade("n/a") == "unknown"


def test_grade_options_include_plus_variants():
    assert "junior+" in GRADE_OPTIONS
    assert "staff" in GRADE_OPTIONS
