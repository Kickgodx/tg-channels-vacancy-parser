from jobdesk.extraction.grade_extractor import extract_grade


def test_senior_over_middle():
    assert extract_grade("We need middle or senior python engineer") == "senior"


def test_lead():
    assert extract_grade("Team Lead position") == "lead"


def test_unknown_when_no_grade():
    assert extract_grade("просто текст вакансии без ключевых слов грейда") == "unknown"


def test_staff_verb_not_grade():
    text = (
        "Senior QA Mobile Engineer. We connect IT professionals to staff and grow "
        "technology teams. hybrid working model."
    )
    assert extract_grade(text) == "senior"


def test_staff_engineer_title():
    assert extract_grade("Staff Engineer, platform team") == "staff"
