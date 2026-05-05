from tg_jobs_parser.extraction.normalizer import normalize_for_match


def test_remote_aliases():
    t = normalize_for_match("Формат: Удалёнка, зарплата 250к ₽")
    assert "удаленк" in t
    assert "rub" in t
