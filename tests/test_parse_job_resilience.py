"""Parse-джоба: сбой необязательной записи не роняет прогон; статус и вакансии пишутся атомарно."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest

from backend.db import parse_run_repository as pr
from backend.db.models import VacancyRow
from backend.db.session import init_db, reset_engine, session_scope
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
def _sqlite_db(monkeypatch, tmp_path):
    db_file = tmp_path / "parse_resilience.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file.as_posix()}")
    get_settings.cache_clear()
    reset_engine()
    init_db()
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


def test_incomplete_run_is_failed_but_keeps_vacancies(monkeypatch, run_id):
    """LLM отключился в primary: собранное сохраняем, но статус не completed."""
    vacancy = _vacancy()
    reason = "AI: LLM отключён (дневной лимит); в режиме primary не разобрано постов: 7."
    result = _result(vacancy).model_copy(update={"incomplete_reason": reason})
    monkeypatch.setattr(parse_tasks, "run_parse", lambda config, **kwargs: result)

    run_parse_job(str(run_id))

    status, error_message, run_params = _run_row(run_id)
    assert status == "failed"
    assert error_message == reason
    assert run_params["vacancies_inserted"] == 1
    with session_scope() as session:
        assert session.get(VacancyRow, vacancy.id) is not None
        assert pr.has_active_parse_run(session) is False
