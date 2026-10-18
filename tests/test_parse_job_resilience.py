"""Parse-джоба: сбой необязательной записи не роняет прогон; статус и вакансии пишутся атомарно."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.db import parse_run_repository as pr
from backend.db.models import VacancyRow, VacancySightingRow
from backend.db.session import reset_engine, session_scope
from backend.settings import get_settings
from backend.workers import parse_tasks
from backend.workers.parse_tasks import run_parse_job
from tg_jobs_parser.config.loader import load_config
from tg_jobs_parser.models.metrics import MetricsSummary
from tg_jobs_parser.models.parse_result import ParseResult
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parse_progress import ParseProgressSnapshot
from tg_jobs_parser.utils.hashing import vacancy_content_id


@pytest.fixture(autouse=True)
def _db(monkeypatch, pg_db):
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def run_id(default_test_config) -> uuid.UUID:
    snapshot = load_config(default_test_config).model_dump(mode="json")
    with session_scope() as session:
        return pr.create_parse_run(session, config_snapshot=snapshot).id


def _vacancy(text: str = "Python dev remote") -> Vacancy:
    return Vacancy(
        id=vacancy_content_id(text),
        source_post_key="chan-1",
        source_channel="chan",
        source_url="https://t.me/s/chan",
        post_url="https://t.me/chan/1",
        post_date=datetime.now(UTC),
        raw_text=text,
        cleaned_text=text,
        detected_specializations=["Python"],
        detected_grade="middle",
    )


def _result(*vacancies: Vacancy) -> ParseResult:
    return ParseResult(
        run_params={"source": "test"},
        posts_fetched=len(vacancies),
        vacancies_all=list(vacancies),
        vacancies_unique=list(vacancies),
        parse_warnings="",
        metrics=MetricsSummary(),
    )


def _boom(*args, **kwargs):
    raise RuntimeError("db down")


def _run_row(run_id: uuid.UUID) -> tuple[str, str | None, dict]:
    with session_scope() as session:
        row = pr.get_parse_run(session, run_id)
        assert row is not None
        return row.status, row.error_message, dict(row.run_params or {})


def test_progress_write_failure_does_not_fail_run(monkeypatch, run_id):
    def fake_run_parse(config, **kwargs):
        kwargs["progress_callback"](ParseProgressSnapshot(1, 1, 0))
        return _result()

    monkeypatch.setattr(parse_tasks, "run_parse", fake_run_parse)
    monkeypatch.setattr(parse_tasks.repo, "patch_run_progress", _boom)

    run_parse_job(str(run_id))

    assert _run_row(run_id)[0] == "completed"


def test_cancel_poll_survives_db_error(monkeypatch, run_id):
    calls = 0

    def flaky_cancel_check(session, rid):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db down")
        return True

    def fake_run_parse(config, **kwargs):
        cancel = kwargs["cancel"]
        deadline = time.monotonic() + 5.0
        while not cancel.is_cancelled() and time.monotonic() < deadline:
            time.sleep(0.05)
        return _result()

    monkeypatch.setattr(parse_tasks, "run_parse", fake_run_parse)
    monkeypatch.setattr(parse_tasks.repo, "is_parse_run_cancel_requested", flaky_cancel_check)

    run_parse_job(str(run_id))

    assert calls >= 2  # поток пережил первый сбой и опросил ещё раз
    assert _run_row(run_id)[0] == "cancelled"


def test_vacancy_save_failure_fails_run_instead_of_completing(monkeypatch, run_id):
    vacancy = _vacancy()
    monkeypatch.setattr(parse_tasks, "run_parse", lambda config, **kwargs: _result(vacancy))
    monkeypatch.setattr(parse_tasks.repo, "upsert_vacancies", _boom)

    run_parse_job(str(run_id))

    status, error_message, _ = _run_row(run_id)
    assert status == "failed"
    assert "Вакансии не сохранены" in (error_message or "")
    with session_scope() as session:
        assert session.get(VacancyRow, vacancy.id) is None
        assert pr.has_active_parse_run(session) is False


def test_completed_run_has_vacancies_and_upsert_stats(monkeypatch, run_id):
    vacancy = _vacancy()
    monkeypatch.setattr(parse_tasks, "run_parse", lambda config, **kwargs: _result(vacancy))

    run_parse_job(str(run_id))

    status, error_message, run_params = _run_row(run_id)
    assert status == "completed"
    assert error_message is None
    # Счётчики upsert дописываются поверх run_params из результата, а не теряются.
    assert run_params["source"] == "test"
    assert run_params["vacancies_inserted"] == 1
    with session_scope() as session:
        assert session.get(VacancyRow, vacancy.id) is not None


def test_duplicates_get_sightings_whether_flushed_or_finalized(monkeypatch, run_id):
    """Дубль из промежуточной записи и дубль, дошедший только до финализации, — оба в vacancy_sightings."""
    original = _vacancy()
    flushed_dup = original.model_copy(
        update={
            "source_post_key": "other-5",
            "source_channel": "other",
            "is_duplicate": True,
            "duplicate_reason": "normalized_hash",
            "duplicate_of": original.id,
        }
    )
    late_dup = flushed_dup.model_copy(update={"source_post_key": "other-6", "duplicate_reason": "fuzzy:95"})

    def fake_run_parse(config, **kwargs):
        kwargs["vacancy_batch_sink"]([original, flushed_dup])
        return _result(original).model_copy(update={"duplicates": [flushed_dup, late_dup]})

    monkeypatch.setattr(parse_tasks, "run_parse", fake_run_parse)

    run_parse_job(str(run_id))

    assert _run_row(run_id)[0] == "completed"
    with session_scope() as session:
        rows = session.scalars(select(VacancySightingRow)).all()
        got = {r.source_post_key: (r.vacancy_id, r.duplicate_reason, r.parse_run_id) for r in rows}
    assert got == {
        "chan-1": (original.id, None, run_id),
        "other-5": (original.id, "normalized_hash", run_id),
        "other-6": (original.id, "fuzzy:95", run_id),
    }


def test_incomplete_run_is_partial_and_keeps_vacancies(monkeypatch, run_id):
    """LLM отключился в primary: собранное сохраняем, статус partial, а не completed."""
    vacancy = _vacancy()
    reason = "AI: LLM отключён (дневной лимит); в режиме primary не разобрано постов: 7."
    result = _result(vacancy).model_copy(update={"incomplete_reason": reason})
    monkeypatch.setattr(parse_tasks, "run_parse", lambda config, **kwargs: result)

    run_parse_job(str(run_id))

    status, error_message, run_params = _run_row(run_id)
    assert status == "partial"
    assert error_message == reason
    assert run_params["vacancies_inserted"] == 1
    with session_scope() as session:
        assert session.get(VacancyRow, vacancy.id) is not None
        assert pr.has_active_parse_run(session) is False


def test_primary_run_without_llm_fails_before_telegram(monkeypatch, default_test_config):
    """primary + LLM не запустился → failed до вычитки, без вакансий, чекпоинтов и ретрая."""
    import tg_jobs_parser.parse_runner as parse_runner
    from backend.db.models import TelegramChannelCheckpointRow
    from backend.workers.recover_parse import recover_stuck_parse_runs

    cfg = load_config(default_test_config)
    cfg = cfg.model_copy(
        update={
            "ai": cfg.ai.model_copy(update={"enabled": True, "mode": "primary"}),
            "sources": cfg.sources.model_copy(update={"channels": ["chan"]}),
        }
    )
    with session_scope() as session:
        rid = pr.create_parse_run(session, config_snapshot=cfg.model_dump(mode="json")).id

    def broken(*args, **kwargs):
        raise ValueError("OPENROUTER_API_KEY не задан")

    fetches: list[object] = []

    class _Parser:
        def __init__(self) -> None:
            self.cancellation = None
            self.new_checkpoints: dict = {}

        def stream_posts_many(self, channels, on_channel):
            fetches.append(channels)

    monkeypatch.setattr("tg_jobs_parser.ai.enricher.AIEnrichmentService", broken)
    monkeypatch.setattr(parse_runner, "_channel_parser", lambda config, cancellation=None: _Parser())

    run_parse_job(str(rid))
    recover_stuck_parse_runs()

    with session_scope() as session:
        row = pr.get_parse_run(session, rid)
        assert row.status == "failed"
        assert "OPENROUTER_API_KEY не задан" in row.error_message
        assert "primary" in row.error_message
        assert row.posts_fetched == 0
        assert row.attempts == 1
        assert session.scalar(select(VacancyRow.id).limit(1)) is None
        assert session.scalar(select(TelegramChannelCheckpointRow.channel).limit(1)) is None
    assert fetches == []
