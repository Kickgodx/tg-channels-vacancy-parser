from tg_jobs_parser.extraction.grade_extractor import extract_grade


def test_senior_over_middle():
    assert extract_grade("We need middle or senior python engineer") == "senior"


def test_lead():
    assert extract_grade("Team Lead position") == "lead"


def test_unknown_when_no_grade():
    assert extract_grade("просто текст вакансии без ключевых слов грейда") == "unknown"
