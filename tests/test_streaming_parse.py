"""Потоковый run_parse: канал уходит в обработку и в БД, не дожидаясь вычитки остальных."""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

import tg_jobs_parser.parse_runner as parse_runner
from tg_jobs_parser.config.schema import (
    AiConfig,
    AppConfig,
    FxConfig,
    ParserConfig,
    SourcesConfig,
)
from tg_jobs_parser.parse_cancel import ParseCancellationToken, ParseCancelled
from tg_jobs_parser.parsers.base import ChannelCheckpoint, RawPost

CHANNELS = ["alpha", "beta"]


def _post(ch: str, i: int) -> RawPost:
    return RawPost(
        channel_username=ch,
        post_id=str(i),
        text=f"Вакансия {ch} #{i}: Python backend developer, remote, зарплата от {300 + i}k, компания {ch}{i}.",
        post_date=datetime.now(UTC),
        post_url=f"https://t.me/{ch}/{i}",
    )


def _cp(i: int) -> ChannelCheckpoint:
    return ChannelCheckpoint(last_message_id=i, covered_from=date(2026, 10, 1))


def _config(channels: list[str] = CHANNELS, **parser_overrides) -> AppConfig:
    return AppConfig(
        sources=SourcesConfig(channels=channels),
        parser=ParserConfig(
            **{
                "db_flush_every_posts": 1000,
                "db_flush_pending_vacancies": 1000,
                "vacancy_score_threshold": 1,
                "vacancy_min_substantive_score": 1,
                "vacancy_fuzzy_duplicate_threshold": 101,
                **parser_overrides,
            }
        ),
        fx=FxConfig(enabled=False),
    )


class _StreamingParser:
    """Отдаёт каналы по одному; перед каждым следующим вызывает ``before_next``."""

    def __init__(self, posts_by_channel: dict[str, list[RawPost]], *, before_next=None, fail_after=None):
        self.posts_by_channel = posts_by_channel
        self.before_next = before_next
        self.fail_after = fail_after
        self.cancellation: ParseCancellationToken | None = None
        self.new_checkpoints: dict[str, ChannelCheckpoint] = {}
        self.finished = threading.Event()

    def stream_posts_many(self, channels, on_channel):
        try:
            for n, ch in enumerate(channels):
                if self.cancellation is not None:
                    self.cancellation.check()
                if n and self.before_next is not None:
                    self.before_next(ch)
                if self.fail_after is not None and n == self.fail_after:
                    raise RuntimeError("Telegram: соединение потеряно")
                self.new_checkpoints[ch] = _cp(n + 1)
                on_channel(ch, list(self.posts_by_channel.get(ch, [])), None)
        finally:
            self.finished.set()


def _install(monkeypatch, parser: _StreamingParser) -> None:
    def factory(config, cancellation=None):
        parser.cancellation = cancellation
        return parser

    monkeypatch.setattr(parse_runner, "_channel_parser", factory)


def test_channel_is_flushed_and_checkpointed_before_next_is_fetched(monkeypatch):
    """Вычитка «beta» ждёт, пока «alpha» разобран, записан и получил чекпоинт."""
    events: list[str] = []
    alpha_done = threading.Event()
    timed_out: list[str] = []

    def before_next(ch):
        if not alpha_done.wait(timeout=5):
            timed_out.append(ch)

    def sink(batch):
        events.extend(f"vacancy:{v.source_channel}" for v in batch)

    def checkpoint_sink(cps):
        events.extend(f"checkpoint:{ch}" for ch in cps)
        alpha_done.set()

    snaps = []
    parser = _StreamingParser(
        {"alpha": [_post("alpha", i) for i in range(3)], "beta": [_post("beta", 1)]}, before_next=before_next
    )
    _install(monkeypatch, parser)

    result = parse_runner.run_parse(
        _config(progress_batch_posts=1),
        vacancy_batch_sink=sink,
        checkpoint_sink=checkpoint_sink,
        progress_callback=snaps.append,
    )

    assert timed_out == []  # обработка шла, пока вычитка стояла
    assert events.index("checkpoint:alpha") == 3  # после всех трёх вакансий канала
    assert events[:3] == ["vacancy:alpha"] * 3
    assert "checkpoint:beta" in events
    assert len(result.vacancies_unique) == 4
    assert result.channel_checkpoints == {"alpha": _cp(1), "beta": _cp(2)}
    # Пока «beta» не прочитан, прогресс честно говорит «вычитка идёт».
    first = snaps[0]
    assert (first.phase, first.channels_total, first.channels_fetched, first.posts_total) == ("fetching", 2, 1, 3)


def test_checkpoint_not_moved_when_vacancies_were_not_written(monkeypatch):
    moved = []

    def failing_sink(batch):
        raise RuntimeError("db is down")

    _install(monkeypatch, _StreamingParser({"alpha": [_post("alpha", 1)]}))
    result = parse_runner.run_parse(
        _config(channels=["alpha"]), vacancy_batch_sink=failing_sink, checkpoint_sink=moved.append
    )

    assert moved == []  # вакансии канала не в БД — следующий прогон его перечитает
    assert len(result.vacancies_unique) == 1


def test_fetch_failure_keeps_already_processed_channels(monkeypatch):
    written: list[str] = []
    _install(
        monkeypatch,
        _StreamingParser({"alpha": [_post("alpha", 1), _post("alpha", 2)], "beta": [_post("beta", 1)]}, fail_after=1),
    )

    with pytest.raises(RuntimeError, match="соединение потеряно"):
        parse_runner.run_parse(_config(), vacancy_batch_sink=lambda b: written.extend(v.source_channel for v in b))

    assert written == ["alpha", "alpha"]


def test_processing_error_stops_fetch_without_cancelling_run(monkeypatch):
    channels = [f"ch{i}" for i in range(50)]
    parser = _StreamingParser({ch: [_post(ch, 1)] for ch in channels})
    _install(monkeypatch, parser)

    def boom(post, config, **kwargs):
        raise ValueError("bug in rules")

    monkeypatch.setattr(parse_runner, "process_raw_post", boom)
    cancel = ParseCancellationToken()

    with pytest.raises(ValueError, match="bug in rules"):
        parse_runner.run_parse(_config(channels=channels), cancel=cancel)

    assert parser.finished.wait(timeout=5)  # вычитка остановлена, поток не висит
    assert parser.cancellation.is_cancelled()
    assert not cancel.is_cancelled()  # прогон не помечен отменённым пользователем


def test_user_cancel_reaches_fetch(monkeypatch):
    cancel = ParseCancellationToken()

    def before_next(ch):
        cancel.request_cancel()

    parser = _StreamingParser({"alpha": [_post("alpha", 1)], "beta": [_post("beta", 1)]}, before_next=before_next)
    _install(monkeypatch, parser)

    result = parse_runner.run_parse(_config(), cancel=cancel)

    assert parser.cancellation.is_cancelled()
    assert "Прогон прерван пользователем." in (result.parse_warnings or "")
    assert result.channel_checkpoints == {}


def test_background_fetch_blocks_when_queue_is_full():
    """Очередь на один канал: второй ждёт в put, третий не читается, пока обработка стоит."""
    delivered: list[str] = []

    class Fetcher:
        def stream_posts_many(self, channels, on_channel):
            for ch in channels:
                delivered.append(ch)
                on_channel(ch, [], None)

    fetch = parse_runner._BackgroundFetch(Fetcher(), ["a", "b", "c"], buffer_channels=1)
    fetch.start()
    deadline = time.monotonic() + 5
    while len(delivered) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.3)
    assert delivered == ["a", "b"]
    assert not fetch.done.is_set()

    fetch.stop(ParseCancellationToken())
    assert fetch.done.is_set()
    assert isinstance(fetch.error, ParseCancelled)


def test_background_fetch_drains_in_order():
    class Fetcher:
        def stream_posts_many(self, channels, on_channel):
            for ch in channels:
                on_channel(ch, [_post(ch, 1)], None)

    fetch = parse_runner._BackgroundFetch(Fetcher(), ["a", "b", "c"], buffer_channels=1)
    fetch.start()
    got = []
    while not fetch.exhausted:
        item = fetch.get(timeout=0.25)
        if item is not None:
            got.append(item[0])
    assert got == ["a", "b", "c"]
    assert fetch.error is None


def test_child_token_follows_parent_but_not_back():
    parent = ParseCancellationToken()
    child = parent.child()
    child.request_cancel()
    assert child.is_cancelled() and not parent.is_cancelled()

    other = parent.child()
    parent.request_cancel()
    assert other.is_cancelled()
    assert parent.child().is_cancelled()  # созданный после отмены — сразу отменён


@pytest.mark.parametrize(
    ("mode", "errors", "rate_limit_skipped", "lost"),
    [
        ("primary", 0, 0, False),
        ("primary", 1, 0, True),
        ("primary", 0, 1, True),
        ("assist", 3, 3, False),  # в assist такие посты разобраны rules
    ],
)
def test_llm_posts_lost(mode, errors, rate_limit_skipped, lost):
    ai = SimpleNamespace(stats=SimpleNamespace(errors=errors, rate_limit_skipped=rate_limit_skipped))
    assert parse_runner._llm_posts_lost(AppConfig(ai=AiConfig(mode=mode)), ai) is lost
    assert parse_runner._llm_posts_lost(AppConfig(ai=AiConfig(mode=mode)), None) is False
