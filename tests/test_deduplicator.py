from datetime import UTC, datetime

from tg_jobs_parser.config.schema import AppConfig, ParserConfig
from tg_jobs_parser.deduplication.deduplicator import deduplicate_vacancies
from tg_jobs_parser.models.vacancy import Vacancy


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
