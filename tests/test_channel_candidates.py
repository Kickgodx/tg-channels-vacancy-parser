"""Кандидаты в источники по пересылкам: разбор fwd_from, сбор в прогоне, запись, выдача админу."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace

import jobdesk.parse_runner as parse_runner
from backend.db.channel_candidate_repository import list_candidates, save_forwards
from backend.db.models import TelegramChannelCheckpointRow, TelegramForwardRow
from backend.db.session import session_scope
from backend.settings import get_settings
from jobdesk.config.schema import AppConfig, FxConfig, ParserConfig, SourcesConfig
from jobdesk.parsers.base import ForwardedPost, ForwardSource, RawPost
from jobdesk.parsers.telegram_telethon import forward_source

_DAY = datetime(2026, 10, 20, 12, tzinfo=UTC)


# --- разбор пересылки Telethon


def _msg(from_id=None, chat=None, channel_post=None):
    fwd = SimpleNamespace(from_id=from_id, channel_post=channel_post)
    return SimpleNamespace(fwd_from=fwd, forward=SimpleNamespace(chat=chat))


def test_forward_source_from_channel():
    src = forward_source(
        _msg(SimpleNamespace(channel_id=777), SimpleNamespace(username="Jobs_Hub", title="Jobs Hub"), 42)
    )
    assert src == ForwardSource(channel_id=777, username="jobs_hub", title="Jobs Hub", post_id=42)


def test_forward_source_multiple_usernames_and_no_entity():
    chat = SimpleNamespace(
        username=None,
        title="T",
        usernames=[SimpleNamespace(username="old", active=False), SimpleNamespace(username="New", active=True)],
    )
    assert forward_source(_msg(SimpleNamespace(channel_id=1), chat)).username == "new"
    bare = forward_source(_msg(SimpleNamespace(channel_id=2), None))
    assert bare == ForwardSource(channel_id=2)


def test_forward_source_ignores_users_hidden_and_plain_posts():
    assert forward_source(_msg(SimpleNamespace(user_id=5))) is None  # переслано от пользователя
    assert forward_source(_msg(None)) is None  # скрытый отправитель
    assert forward_source(SimpleNamespace(fwd_from=None)) is None


# --- сбор в прогоне


class _FakeParser:
    def __init__(self, posts):
        self._posts = posts

    def fetch_posts_many(self, channels):
        return [(ch, list(self._posts), None) for ch in channels]


def _post(i: int, *, text: str, source: ForwardSource | None) -> RawPost:
    return RawPost(
        channel_username="chan",
        post_id=str(i),
        text=text,
        post_date=_DAY,
        post_url=f"https://t.me/chan/{i}",
        forward_from=source,
    )


def test_run_parse_collects_forwarded_posts(monkeypatch):
    hub = ForwardSource(channel_id=10, username="hub")

    def vacancy(i: int) -> str:
        return f"Вакансия #{i}: Python backend developer, remote, зарплата от {300 + i}k, компания Acme{i}."

    posts = [
        _post(1, text=vacancy(1), source=hub),
        _post(2, text="Котики и погода, ничего про работу", source=hub),
        _post(3, text=vacancy(3), source=None),
        _post(4, text=vacancy(4), source=ForwardSource(channel_id=11, username="chan")),  # из себя
    ]
    cfg = AppConfig(
        sources=SourcesConfig(channels=["Chan"]),
        parser=ParserConfig(vacancy_score_threshold=1, vacancy_min_substantive_score=1),
        fx=FxConfig(enabled=False),
    )
    monkeypatch.setattr(parse_runner, "_channel_parser", lambda config, cancellation=None: _FakeParser(posts))
    result = parse_runner.run_parse(cfg)
    got = sorted((f.post_id, f.source.username, f.is_vacancy) for f in result.forwarded_posts)
    assert got == [("1", "hub", True), ("2", "hub", False)]
    assert "forwarded_posts" not in result.model_dump()


# --- запись и выдача


def _fwd(post_id: str, source: ForwardSource, *, channel="chan", vacancy=True) -> ForwardedPost:
    return ForwardedPost(channel=channel, post_id=post_id, post_date=_DAY, source=source, is_vacancy=vacancy)


def test_save_forwards_is_idempotent_and_keeps_known_names(pg_db):
    hub = ForwardSource(channel_id=10, username="hub", title="Hub", post_id=5)
    with session_scope() as s:
        save_forwards(s, [_fwd("1", hub, vacancy=False), _fwd("2", hub), _fwd("1", hub, channel="other")])
    # Перечитали то же окно: пост 1 теперь прошёл как вакансия, имени канала в этот раз нет.
    with session_scope() as s:
        save_forwards(s, [_fwd("1", ForwardSource(channel_id=10), vacancy=True)])
    with session_scope() as s:
        assert s.query(TelegramForwardRow).count() == 3
        [c] = list_candidates(s, known=set(), include_ignored=False)
    assert (c["username"], c["title"], c["forwards"], c["vacancy_forwards"]) == ("hub", "Hub", 3, 3)
    assert c["seen_in"] == ["chan", "other"]
    assert c["example_url"] == "https://t.me/hub/5"  # id поста в источнике — из первой записи


def test_list_skips_known_private_and_orders_by_vacancies(pg_db):
    a = ForwardSource(channel_id=1, username="a_jobs")
    b = ForwardSource(channel_id=2, username="b_jobs")
    private = ForwardSource(channel_id=3, title="Закрытый")
    known = ForwardSource(channel_id=4, username="Known")
    with session_scope() as s:
        save_forwards(
            s,
            [
                _fwd("1", a, vacancy=False),
                _fwd("2", a, vacancy=False),
                _fwd("3", b),
                _fwd("4", private),
                _fwd("5", known),
            ],
        )
        rows = list_candidates(s, known={"known"}, include_ignored=False)
    assert [r["username"] for r in rows] == ["b_jobs", "a_jobs"]


def test_api_admin_only_excludes_read_channels_and_ignores(admin_client):
    with session_scope() as s:
        save_forwards(
            s,
            [
                _fwd("1", ForwardSource(channel_id=1, username="hub")),
                _fwd("2", ForwardSource(channel_id=2, username="test_channel")),  # в config.yaml тестов
                _fwd("3", ForwardSource(channel_id=3, username="checkpointed")),
            ],
        )
        s.add(TelegramChannelCheckpointRow(channel="Checkpointed", last_message_id=1, covered_from=_DAY.date()))

    r = admin_client.get("/api/v1/channels/candidates")
    assert r.status_code == 200, r.text
    assert [c["username"] for c in r.json()["items"]] == ["hub"]

    assert admin_client.patch("/api/v1/channels/candidates/1", json={"status": "ignored"}).status_code == 200
    assert admin_client.get("/api/v1/channels/candidates").json()["items"] == []
    shown = admin_client.get("/api/v1/channels/candidates?include_ignored=true").json()["items"]
    assert [(c["username"], c["status"]) for c in shown] == [("hub", "ignored")]
    assert admin_client.patch("/api/v1/channels/candidates/99", json={"status": "new"}).status_code == 404
    assert admin_client.patch("/api/v1/channels/candidates/1", json={"status": "added"}).status_code == 422

    admin_client.headers.pop("Authorization")
    user = admin_client.post("/api/v1/auth/register", json={"email": "u@example.com", "password": "secret1"})
    h = {"Authorization": f"Bearer {user.json()['access_token']}"}
    assert admin_client.get("/api/v1/channels/candidates", headers=h).status_code == 403


def test_parse_job_saves_forwards(admin_client, monkeypatch):
    def fake_run_parse(config, **kwargs):
        from jobdesk.models.parse_result import ParseResult

        return ParseResult(posts_fetched=1, forwarded_posts=[_fwd("1", ForwardSource(channel_id=1, username="hub"))])

    monkeypatch.setenv("PARSE_JOB_BACKEND", "inprocess")
    get_settings.cache_clear()
    monkeypatch.setattr("backend.workers.parse_tasks.run_parse", fake_run_parse)
    run_id = admin_client.post("/api/v1/runs", json={}).json()["run_id"]
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if admin_client.get(f"/api/v1/runs/{run_id}").json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    items = admin_client.get("/api/v1/channels/candidates").json()["items"]
    assert [(c["username"], c["forwards"]) for c in items] == [("hub", 1)]
