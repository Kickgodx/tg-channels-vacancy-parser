"""Промежуточная запись вакансий в БД (vacancy_batch_sink) во время run_parse."""

from __future__ import annotations

from datetime import datetime, timezone

import tg_jobs_parser.parse_runner as parse_runner
from tg_jobs_parser.config.schema import (
    AppConfig,
    FxConfig,
    OutputConfig,
    ParserConfig,
    SourcesConfig,
)
from tg_jobs_parser.deduplication.deduplicator import (
    VacancyDeduplicator,
    deduplicate_vacancies,
)
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parsers.base import RawPost


def _v(text: str, vid: str) -> Vacancy:
    return Vacancy(
        id=vid,
        source_post_key=vid,
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_url="https://t.me/ch/1",
        post_date=datetime.now(timezone.utc),
        raw_text=text,
        cleaned_text=text,
        detected_specializations=["Python"],
        vacancy_score=5,
        vacancy_substantive_score=3,
    )


def _post(i: int, text: str | None = None) -> RawPost:
    body = text or (
        f"Вакансия #{i}: Python backend developer, remote, "
        f"зарплата от {300 + i}k, компания Acme{i}."
    )
    return RawPost(
        channel_username="chan",
        post_id=str(i),
        text=body,
        post_date=datetime.now(timezone.utc),
        post_url=f"https://t.me/chan/{i}",
    )


class _FakeChannelParser:
    def __init__(self, posts: list[RawPost]) -> None:
        self._posts = posts

    def fetch_posts_many(self, channels):
        return [(ch, list(self._posts), None) for ch in channels]


def _config(**parser_overrides) -> AppConfig:
    parser_kwargs = {
        "db_flush_every_posts": 2,
        "db_flush_pending_vacancies": 1000,
        "vacancy_score_threshold": 1,
        "vacancy_min_substantive_score": 1,
        # fuzzy-дедуп не тестируем: иначе шаблонные тексты сольются в дубликаты.
        "vacancy_fuzzy_duplicate_threshold": 101,
        **parser_overrides,
    }
    return AppConfig(
        sources=SourcesConfig(channels=["chan"]),
        parser=ParserConfig(**parser_kwargs),
        output=OutputConfig(save_progress_batches=False),
        fx=FxConfig(enabled=False),
    )


def _run(monkeypatch, posts: list[RawPost], sink, **parser_overrides):
    cfg = _config(**parser_overrides)
    monkeypatch.setattr(
        parse_runner,
        "_channel_parser",
        lambda config, cancellation=None: _FakeChannelParser(posts),
    )
    return parse_runner.run_parse(cfg, vacancy_batch_sink=sink)


def test_incremental_dedup_matches_one_shot():
    cfg = AppConfig()
    batch1 = [
        _v("python developer remote москва зарплата 300к", "a"),
        _v("senior java engineer office berlin 5000 eur", "b"),
    ]
    batch2 = [
        _v("python developer remote москва зарплата 300к", "a2"),
        _v("qa automation tester selenium pytest удаленка", "c"),
    ]

    ded = VacancyDeduplicator(cfg)
    ded.add(batch1)
    ded.add(batch2)

    uniq_ids = [v.id for v in ded.unique]
    dup_ids = [v.id for v in ded.duplicates]
    assert uniq_ids == ["a", "b", "c"]
    assert dup_ids == ["a2"]

    one_uniq, one_dups = deduplicate_vacancies(batch1 + batch2, cfg)
    assert [v.id for v in one_uniq] == uniq_ids
    assert [v.id for v in one_dups] == dup_ids


def test_sink_receives_unique_batches_during_run(monkeypatch):
    posts = [_post(i) for i in range(6)]
    posts.append(_post(6, text=posts[0].text))  # точный дубликат первого поста

    batches: list[list[Vacancy]] = []
    result = _run(monkeypatch, posts, batches.append)

    assert len(batches) >= 2  # сливы по ходу цикла + финальный
    flushed_ids = [v.id for batch in batches for v in batch]
    assert len(flushed_ids) == len(set(flushed_ids))  # без повторов между батчами
    assert set(flushed_ids) == {v.id for v in result.vacancies_unique}
    assert len(result.vacancies_unique) == 6
    assert len(result.duplicates) == 1


def test_flush_triggers_on_pending_vacancies_threshold(monkeypatch):
    """Флаш по числу накопленных уникальных вакансий, даже если лимит постов не достигнут."""
    posts = [_post(i) for i in range(7)]

    batches: list[list[Vacancy]] = []
    result = _run(
        monkeypatch,
        posts,
        batches.append,
        db_flush_every_posts=10_000,  # триггер по постам никогда не сработает
        db_flush_pending_vacancies=3,
    )

    # 3+3 по порогу вакансий + 1 финальный = минимум 3 вызова sink
    assert len(batches) >= 3
    flushed_ids = [v.id for batch in batches for v in batch]
    assert set(flushed_ids) == {v.id for v in result.vacancies_unique}
    assert len(result.vacancies_unique) == 7


def test_sink_failure_does_not_break_run(monkeypatch):
    posts = [_post(i) for i in range(5)]

    def bad_sink(batch: list[Vacancy]) -> None:
        raise RuntimeError("db is down")

    result = _run(monkeypatch, posts, bad_sink)

    assert len(result.vacancies_unique) == 5


def test_sink_parallel_path(monkeypatch):
    posts = [_post(i) for i in range(205)]

    batches: list[list[Vacancy]] = []
    result = _run(monkeypatch, posts, batches.append)

    flushed_ids = [v.id for batch in batches for v in batch]
    assert set(flushed_ids) == {v.id for v in result.vacancies_unique}
    assert len(flushed_ids) == len(set(flushed_ids))
    assert len(result.vacancies_unique) == 205


def test_no_sink_still_returns_full_result(monkeypatch):
    posts = [_post(i) for i in range(5)]

    cfg = _config()
    monkeypatch.setattr(
        parse_runner,
        "_channel_parser",
        lambda config, cancellation=None: _FakeChannelParser(posts),
    )
    result = parse_runner.run_parse(cfg)

    assert len(result.vacancies_unique) == 5
