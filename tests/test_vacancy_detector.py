from tg_jobs_parser.extraction.vacancy_detector import compute_vacancy_score


def test_hiring_post_scores():
    score, _, substantive = compute_vacancy_score(
        "Вакансия: Python developer remote, salary 3000-4000 USD, contact @hr_bot",
    )
    assert score >= 3
    assert substantive >= 1


def test_resume_excluded_zero():
    score, reason, substantive = compute_vacancy_score("Ищу работу, приложил резюме")
    assert score == 0
    assert substantive == 0
    assert reason == "exclude_keywords"


def test_contact_only_high_total_low_substantive():
    _, _, substantive = compute_vacancy_score("что-то чат @gogetajob просто упомянули")
    assert substantive == 0
