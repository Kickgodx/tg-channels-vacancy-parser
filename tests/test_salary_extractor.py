from jobdesk.extraction.salary_extractor import extract_salary


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


def test_job_url_id_is_not_salary():
    """Регресс: id вакансии в URL не должен быть зарплатой."""
    text = "Golang backend developer. Контакты: https://job.2gis.ru/software/76348/ Опыт от 5 лет."
    r = extract_salary(text)
    assert r.min_value is None
    assert r.max_value is None


def test_hourly_rub_per_hour_range():
    text = "Оплата: 2 000–4 000 ₽/час. Удалённо."
    r = extract_salary(text)
    assert r.min_value == 2000.0
    assert r.max_value == 4000.0
    assert r.currency == "RUB/HOUR"


def test_hourly_rub_per_hour_single():
    text = "Senior Manual QA 1 000 ₽ / час удалённо"
    r = extract_salary(text)
    assert r.min_value == 1000.0
    assert r.currency == "RUB/HOUR"


def test_hourly_rub_slash_chas_text():
    text = "Оплата: 2000 руб/час full-time"
    r = extract_salary(text)
    assert r.min_value == 2000.0
    assert r.currency == "RUB/HOUR"


def test_hourly_usd_range_in_text():
    text = "Условия и з.п. от $15 до $30 в час. Формат: Фриланс, почасовая оплата раз в месяц."
    r = extract_salary(text)
    assert r.min_value == 15.0
    assert r.max_value == 30.0
    assert r.avg_value == 22.5
    assert r.currency == "USD/HOUR"


def test_experience_range_is_not_salary():
    """Диапазон 'опыт 3-5 лет' без валюты — не зарплата."""
    r = extract_salary("Требуется senior, опыт 3-5 лет работы с PostgreSQL.")
    assert r.min_value is None and r.max_value is None


def test_monthly_tr_range_not_hourly():
    text = "Вилка: от 200 тр до 300 тр. критерии перевода в регресс) в части контроля качества"
    r = extract_salary(text)
    assert r.min_value == 200_000.0
    assert r.max_value == 300_000.0
    assert r.avg_value == 250_000.0
    assert r.currency == "RUB"


def test_monthly_k_gross_pair_not_hourly():
    text = "Вилка: 300к гросс по ТК и 320к гросс по ИП, удалённо."
    r = extract_salary(text)
    assert r.min_value == 300_000.0
    assert r.max_value == 320_000.0
    assert r.avg_value == 310_000.0
    assert r.currency == "RUB"


def test_net_inside_word_is_not_salary_anchor():
    """Регресс: «net» внутри слова не якорь — иначе «Posted: 08 Oct» давало зарплату 8."""
    text = "Network & Internet forensics @ Acme 🗓️ Posted: 08 Oct 2026 💼 Employment: Full-time"
    r = extract_salary(text)
    assert r.min_value is None and r.max_value is None


def test_dotnet_version_is_not_salary():
    r = extract_salary("Интересуетесь технологиями, такими как .NET 10 и Blazor")
    assert r.min_value is None


def test_range_with_tys_suffix_is_scaled():
    """Регресс: «ЗП до 180–260 тыс.» давало 180."""
    r = extract_salary("Через год: • ЗП растёт до 180–260 тыс. • Переход в middle")
    assert r.min_value == 180_000.0
    assert r.max_value == 260_000.0


def test_anchored_with_tys_suffix_is_scaled():
    r = extract_salary("Вилка: от 250 тыс. на руки")
    assert r.min_value == 250_000.0


def test_small_anchored_number_without_currency_is_not_salary():
    r = extract_salary("Зарплата: обсудим. Gross 7 дней отпуска сверху")
    assert r.min_value is None


def test_comma_thousands_separator():
    """Регресс: «$120,000» читалось как 120."""
    r = extract_salary("ЗП: ~$120,000 – $140,000 в год (гросс)")
    assert r.min_value == 120_000.0
    assert r.currency == "USD"


def test_dot_thousands_separator_range():
    r = extract_salary("Уровень дохода: 60.000-80.000")
    assert r.min_value == 60_000.0
    assert r.max_value == 80_000.0


def test_hourly_range_without_currency_sign_is_rub():
    r = extract_salary("Оплата 400 - 550/ч, парт-тайм")
    assert r.min_value == 400.0
    assert r.max_value == 550.0
    assert r.currency == "RUB/HOUR"
