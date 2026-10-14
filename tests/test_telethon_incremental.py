"""Telethon: чекпоинты каналов (min_id), offset_date для окна в прошлом, FloodWait и частичный обход."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError

from tg_jobs_parser.config.schema import AppConfig, ParserConfig, SearchConfig
from tg_jobs_parser.parsers.base import ChannelCheckpoint
from tg_jobs_parser.parsers.telegram_telethon import TelegramTelethonParser

TODAY = datetime.now(UTC).date()
WEEK_AGO = TODAY - timedelta(days=7)


def _msg(mid: int, day: date, text: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=mid, message=text or f"post {mid}", date=datetime.combine(day, time(12), tzinfo=UTC))


# id 1..10: по одному сообщению в день, id 10 — сегодня, id 1 — 9 дней назад.
MESSAGES = [_msg(i, TODAY - timedelta(days=10 - i)) for i in range(1, 11)]


class FakeClient:
    """``iter_messages`` как в Telethon: от новых к старым, с min_id / offset_id / offset_date."""

    def __init__(self, messages, *, fail_after: int | None = None, error: Exception | None = None):
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.fail_after = fail_after
        self.error = error
        self.calls: list[dict] = []

    async def iter_messages(self, username, *, limit=None, offset_date=None, offset_id=0, min_id=0):
        self.calls.append({"limit": limit, "offset_date": offset_date, "offset_id": offset_id, "min_id": min_id})
        yielded = 0
        for m in self.messages:
            if min_id and m.id <= min_id:
                continue
            if offset_id and m.id >= offset_id:
                continue
            if offset_date and m.date >= offset_date:
                continue
            if self.error is not None and self.fail_after is not None and yielded == self.fail_after:
                err, self.error = self.error, None  # одна ошибка за тест
                raise err
            yielded += 1
            yield m


def _parser(*, date_from=WEEK_AGO, date_to=TODAY, checkpoints=None, **parser_kw) -> TelegramTelethonParser:
    cfg = AppConfig(
        search=SearchConfig(date_from=date_from.isoformat(), date_to=date_to.isoformat()),
        parser=ParserConfig(**parser_kw),
    )
    p = TelegramTelethonParser(cfg)
    p.checkpoints = dict(checkpoints or {})
    return p


def _run(parser, client):
    return asyncio.run(parser._iter_channel(client, "chan"))


def _ids(posts) -> list[int]:
    return sorted(int(p.post_id) for p in posts)


def test_full_window_records_checkpoint_from_date_from():
    parser = _parser()
    posts, warn = _run(parser, FakeClient(MESSAGES))
    assert warn is None
    assert _ids(posts) == [3, 4, 5, 6, 7, 8, 9, 10]  # 7 дней назад и новее
    assert parser.new_checkpoints["chan"] == ChannelCheckpoint(last_message_id=10, covered_from=WEEK_AGO)


def test_checkpoint_reads_only_newer_messages_and_keeps_coverage():
    old = ChannelCheckpoint(last_message_id=8, covered_from=WEEK_AGO - timedelta(days=3))
    parser = _parser(checkpoints={"chan": old})
    client = FakeClient(MESSAGES)
    posts, _ = _run(parser, client)
    assert client.calls[0]["min_id"] == 8
    assert _ids(posts) == [9, 10]
    # Дошли до min_id, не до date_from: покрытие сплошное с прошлым чекпоинтом.
    assert parser.new_checkpoints["chan"] == ChannelCheckpoint(last_message_id=10, covered_from=old.covered_from)


def test_checkpoint_not_covering_date_from_is_ignored():
    old = ChannelCheckpoint(last_message_id=8, covered_from=WEEK_AGO + timedelta(days=1))
    parser = _parser(checkpoints={"chan": old})
    client = FakeClient(MESSAGES)
    posts, _ = _run(parser, client)
    assert client.calls[0]["min_id"] == 0
    assert _ids(posts) == [3, 4, 5, 6, 7, 8, 9, 10]
    assert parser.new_checkpoints["chan"].covered_from == WEEK_AGO


def test_incremental_fetch_disabled_ignores_checkpoint():
    old = ChannelCheckpoint(last_message_id=8, covered_from=WEEK_AGO - timedelta(days=3))
    parser = _parser(checkpoints={"chan": old}, incremental_fetch=False)
    client = FakeClient(MESSAGES)
    posts, _ = _run(parser, client)
    assert client.calls[0]["min_id"] == 0
    assert len(posts) == 8


def test_no_new_messages_keeps_checkpoint_id():
    old = ChannelCheckpoint(last_message_id=10, covered_from=WEEK_AGO)
    parser = _parser(checkpoints={"chan": old})
    posts, _ = _run(parser, FakeClient(MESSAGES))
    assert posts == []
    assert parser.new_checkpoints["chan"] == old


def test_date_to_in_past_uses_offset_date_and_no_checkpoint():
    date_to = TODAY - timedelta(days=3)
    parser = _parser(date_from=TODAY - timedelta(days=5), date_to=date_to)
    client = FakeClient(MESSAGES)
    posts, _ = _run(parser, client)
    assert client.calls[0]["offset_date"] == datetime.combine(date_to, time.max, tzinfo=UTC)
    assert _ids(posts) == [5, 6, 7]
    assert parser.new_checkpoints == {}


def test_scan_limit_hit_gives_no_checkpoint():
    parser = _parser(max_posts_per_channel=3)
    posts, _ = _run(parser, FakeClient(MESSAGES))
    assert _ids(posts) == [8, 9, 10]
    assert parser.new_checkpoints == {}


def test_short_flood_wait_is_waited_and_resumed(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(self, seconds):
        slept.append(seconds)
        return True

    monkeypatch.setattr(TelegramTelethonParser, "_sleep_cancellable", fake_sleep)
    parser = _parser(flood_wait_max_seconds=120)
    client = FakeClient(MESSAGES, fail_after=2, error=FloodWaitError(request=None, capture=90))
    posts, warn = _run(parser, client)
    assert slept == [90]
    assert warn is None
    assert client.calls[1]["offset_id"] == 9  # продолжили со сообщений старше последнего прочитанного
    assert _ids(posts) == [3, 4, 5, 6, 7, 8, 9, 10]
    assert parser.new_checkpoints["chan"].last_message_id == 10


def test_long_flood_wait_returns_partial_posts_with_warning():
    parser = _parser(flood_wait_max_seconds=60)
    client = FakeClient(MESSAGES, fail_after=2, error=FloodWaitError(request=None, capture=3600))
    posts, warn = _run(parser, client)
    assert _ids(posts) == [9, 10]
    assert warn is not None and "FloodWait 3600" in warn
    assert parser.new_checkpoints == {}


def test_error_mid_scan_keeps_read_posts():
    parser = _parser()
    client = FakeClient(MESSAGES, fail_after=3, error=ConnectionError("reset"))
    posts, warn = _run(parser, client)
    assert _ids(posts) == [8, 9, 10]
    assert warn is not None and "частично" in warn
    assert parser.new_checkpoints == {}


def test_error_before_any_post_propagates():
    parser = _parser()
    client = FakeClient(MESSAGES, fail_after=0, error=ConnectionError("reset"))
    with pytest.raises(ConnectionError):
        _run(parser, client)
