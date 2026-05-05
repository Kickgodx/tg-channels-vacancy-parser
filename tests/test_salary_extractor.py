from tg_jobs_parser.extraction.salary_extractor import extract_salary


def test_salary_range_usd():
    r = extract_salary("Compensation 3000-5000 USD for senior")
    assert r.min_value and r.max_value
    assert r.currency in ("USD", "usd", None) or r.currency == "USD"


def test_salary_ruble_k():
    r = extract_salary("от 250к net на руки")
    assert r.min_value is not None


def test_single_dollar_suffix():
    r = extract_salary("Rate 3000$")
    assert r.min_value == 3000.0
