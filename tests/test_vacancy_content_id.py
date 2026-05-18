from tg_jobs_parser.extraction.normalizer import normalize_for_match
from tg_jobs_parser.utils.hashing import vacancy_content_id


def test_vacancy_content_id_stable_for_formatting():
    a = "Python  dev\n\nRemote  "
    b = "python dev remote"
    assert vacancy_content_id(a) == vacancy_content_id(b)
    assert vacancy_content_id(a) == vacancy_content_id(normalize_for_match(a))


def test_vacancy_content_id_differs_for_different_text():
    assert vacancy_content_id("job a") != vacancy_content_id("job b")
