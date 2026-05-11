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


def test_phone_number_is_not_salary():
    """Регресс: длинный международный телефон не должен ловиться как зарплата."""
    text = (
        "Senior AI Engineer Заработная плата по договоренности "
        "Обязанности: Telegram bot flows. "
        "Контакты: +998949983000 hello@widerai.us"
    )
    r = extract_salary(text)
    assert r.min_value is None
    assert r.max_value is None
    assert r.avg_value is None


def test_negotiable_salary_returns_empty():
    r = extract_salary("Зарплата по договоренности, обсуждается с финалистами.")
    assert r.min_value is None and r.max_value is None


def test_negotiable_english_returns_empty():
    r = extract_salary("Salary: negotiable, depends on experience. Contact +1 555 123 4567")
    assert r.min_value is None and r.max_value is None


def test_bare_number_is_salary():
    """Голое число без валюты — всё равно валидная зарплата (типичные ₽-объявления)."""
    r = extract_salary("Senior backend developer. 150000 на руки, удалёнка.")
    assert r.min_value == 150000.0


def test_bare_number_below_threshold_ignored():
    """Маленькие числа (опыт/часы/проценты/количество людей) — не зарплата."""
    r = extract_salary("Команда из 50 человек, опыт 3-5 лет, дедлайн 2 недели.")
    assert r.min_value is None
    assert r.max_value is None


def test_year_is_not_salary():
    """Год основания/работы не должен трактоваться как зарплата."""
    r = extract_salary("Компания работает с 2015 года, обсудим условия.")
    assert r.min_value is None and r.max_value is None


def test_salary_with_keyword_anchor_no_currency():
    """Якорное слово 'оклад' рядом с числом — берём как зарплату."""
    r = extract_salary("Оклад 150000 на руки, офис в Москве.")
    assert r.min_value == 150000.0


def test_russian_phone_not_salary():
    r = extract_salary("Звоните: 8 (495) 123-45-67. Зарплата обсуждается.")
    assert r.min_value is None and r.max_value is None


def test_experience_range_is_not_salary():
    """Диапазон 'опыт 3-5 лет' без валюты — не зарплата."""
    r = extract_salary("Требуется senior, опыт 3-5 лет работы с PostgreSQL.")
    assert r.min_value is None and r.max_value is None
