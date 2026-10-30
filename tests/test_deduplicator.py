from datetime import UTC, datetime, timedelta

from jobdesk.config.schema import AppConfig, ParserConfig
from jobdesk.deduplication.deduplicator import VacancyDeduplicator, deduplicate_vacancies
from jobdesk.models.vacancy import Vacancy


def _v(text: str, vid: str) -> Vacancy:
    return Vacancy(
        id=vid,
        source_post_key=vid,
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_url="https://t.me/ch/1",
        post_date=datetime.now(UTC),
        raw_text=text,
        cleaned_text=text,
        detected_specializations=["Python"],
        vacancy_score=5,
        vacancy_substantive_score=3,
    )


def test_exact_duplicate():
    cfg = AppConfig(parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=99))
    a = _v("same text", "ch-1")
    b = _v("same text", "ch-2")
    uniq, dups = deduplicate_vacancies([a, b], cfg)
    assert len(uniq) == 1
    assert len(dups) == 1
    assert dups[0].is_duplicate


def _spec_v(text: str, vid: str, specs: list[str]) -> Vacancy:
    v = _v(text, vid)
    v.detected_specializations = specs
    return v


_NEAR_A = "Ищем Python backend разработчика в финтех, удалёнка, зарплата 300к, пишите @hr_acme"
_NEAR_B = "Ищем Python backend разработчика в финтех, удалёнка, зарплата 300к, пишите @hr_acme!"


def test_fuzzy_duplicate_within_same_specializations():
    cfg = AppConfig(parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=88))
    a = _spec_v(_NEAR_A, "a", ["Python", "Backend"])
    b = _spec_v(_NEAR_B, "b", ["Backend", "Python"])  # тот же набор, другой порядок
    uniq, dups = deduplicate_vacancies([a, b], cfg)
    assert [v.id for v in uniq] == ["a"]
    assert dups[0].duplicate_reason.startswith("fuzzy:")
    assert dups[0].duplicate_group_id == a.duplicate_group_id


def test_fuzzy_blocking_skips_other_specializations():
    """Блокировка: почти-дубль с другим набором специализаций fuzzy не сравнивается."""
    a = _spec_v(_NEAR_A, "a", ["Python"])
    b = _spec_v(_NEAR_B, "b", ["Python", "Backend"])

    blocked = AppConfig(parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=80))
    uniq, dups = deduplicate_vacancies([a, b], blocked)
    assert len(uniq) == 2 and not dups

    a2 = _spec_v(_NEAR_A, "a", ["Python"])
    b2 = _spec_v(_NEAR_B, "b", ["Python", "Backend"])
    unblocked = AppConfig(
        parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=80, fuzzy_dedup_block_by_specializations=False)
    )
    uniq, dups = deduplicate_vacancies([a2, b2], unblocked)
    assert [v.id for v in uniq] == ["a"]
    assert [v.id for v in dups] == ["b"]


def test_exact_duplicate_ignores_blocking():
    """Точный дубль по хэшу текста ловится и при разных специализациях."""
    cfg = AppConfig(parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=101))
    a = _spec_v("same text", "a", ["Python"])
    b = _spec_v("same text", "b", ["Go"])
    uniq, dups = deduplicate_vacancies([a, b], cfg)
    assert len(uniq) == 1
    assert dups[0].duplicate_reason == "exact_raw_hash"


def test_fuzzy_picks_first_group_on_equal_score():
    """При равном счёте — первая по порядку группа (как прежний цикл)."""
    # g1 vs g2 ≈ 94 (< порога) — две группы; кандидат — подмножество обеих (100).
    cfg = AppConfig(parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=97))
    g1 = _spec_v("ищем python разработчика в команду платежей", "g1", ["Python"])
    g2 = _spec_v("ищем python разработчика в команду поиска", "g2", ["Python"])
    cand = _spec_v("ищем python разработчика в команду", "c", ["Python"])
    uniq, dups = deduplicate_vacancies([g1, g2, cand], cfg)
    assert [v.id for v in uniq] == ["g1", "g2"]
    assert dups[0].duplicate_group_id == g1.duplicate_group_id


# --- полный текст, ключи блоков, вакансии прошлых прогонов

_POST = (
    "Компания Acme ищет QA инженера в команду платежей. Удалёнка, полный день. Требования: "
    "pytest, API, SQL, Docker, опыт от 3 лет. Задачи: автотесты, ревью, CI. Зарплата 200 000 ₽. "
    "Пишите в телеграм @acme_hr, ответим за день."
)
_EDITED = _POST.replace("200 000", "240 000")


def _full(text: str, vid: str, *, grade="middle", company="Acme", contact=None, specs=("QA",), days=0) -> Vacancy:
    v = _spec_v(text, vid, list(specs))
    v.detected_grade = grade
    v.company = company
    v.contact = contact
    v.post_date = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(days=days)
    return v


def _cfg(**kw) -> AppConfig:
    # Составной ключ сравнивает и зарплату — правку зарплаты ловит только полный текст.
    return AppConfig(parser=ParserConfig(vacancy_fuzzy_duplicate_threshold=101, **kw))


def test_edited_repost_is_text_duplicate():
    uniq, dups = deduplicate_vacancies([_full(_POST, "a"), _full(_EDITED, "b", days=5)], _cfg())
    assert [v.id for v in uniq] == ["a"]
    assert dups[0].duplicate_of == "a" and dups[0].duplicate_reason.startswith("fuzzy_text:")


def test_text_rule_guards_grade_company_window_and_off():
    cases = [
        _full(_EDITED, "b", grade="senior"),  # шаблон компании: другой грейд — другая позиция
        _full(_EDITED, "b", company="Other"),
        _full(_EDITED, "b", days=41),  # дальше cross_run_dedup_days
    ]
    for b in cases:
        uniq, _ = deduplicate_vacancies([_full(_POST, "a"), b], _cfg())
        assert len(uniq) == 2, b
    uniq, _ = deduplicate_vacancies([_full(_POST, "a"), _full(_EDITED, "b")], _cfg(fuzzy_text_duplicate_threshold=101))
    assert len(uniq) == 2
    # Шаблон компании: другая роль или другой язык при том же тексте — другая позиция.
    analyst = _full(_EDITED, "b", specs=("Analyst",))
    python_dev = _full(_EDITED, "b")
    a_java = _full(_POST, "a")
    a_java.technologies, python_dev.technologies = ["Java", "Docker"], ["Python", "Docker"]
    for a, b in ((_full(_POST, "a"), analyst), (a_java, python_dev)):
        uniq, _ = deduplicate_vacancies([a, b], _cfg(fuzzy_dedup_block_by_specializations=False))
        assert len(uniq) == 2, b.detected_specializations
    # Неизвестный грейд с известным совместим.
    uniq, _ = deduplicate_vacancies([_full(_POST, "a"), _full(_EDITED, "b", grade="unknown")], _cfg())
    assert len(uniq) == 1


def test_contact_block_finds_duplicate_with_other_specializations():
    """Блок по контакту: почти-дубль с другим набором специализаций находится, хотя блок ролей другой."""
    a = _full(_POST, "a", company=None, contact="@acme_hr", specs=("QA",))
    b = _full(_EDITED, "b", company=None, contact="@ACME_HR", specs=("QA", "Automation QA"))
    uniq, dups = deduplicate_vacancies([a, b], _cfg())
    assert [v.id for v in uniq] == ["a"] and dups[0].duplicate_of == "a"


def test_seeded_known_vacancy():
    known = _full(_POST, "db-a")
    d = VacancyDeduplicator(_cfg())
    d.seed([known])
    same1 = _full(_POST, "db-a", days=3)  # тот же текст — та же вакансия из БД
    same2 = _full(_POST, "db-a", days=3)
    edited = _full(_EDITED, "new-b", days=4)
    new_u, new_d = d.add([same1, same2, edited])
    assert new_u == [same1] and not same1.is_duplicate
    assert [(v.duplicate_of, v.duplicate_reason.split(":")[0]) for v in new_d] == [
        ("db-a", "exact_raw_hash"),
        ("db-a", "fuzzy_text"),
    ]
    assert d.unique == [same1]  # сами известные в итог не попадают
