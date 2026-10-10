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
