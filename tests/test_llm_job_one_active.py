"""Одна активная LLM-джоба на пару и лимит на пользователя под параллельными запросами (C8)."""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from tests.conftest import new_user

from backend.db import repository as repo
from backend.db.migrate import alembic_ini_path
from backend.db.models import LlmJobRow, ResumeRow, UserRow, VacancyRow
from backend.db.session import session_scope
from backend.services import llm_job_service
from backend.services.llm_job_service import LlmJobUserLimitError, llm_job_runner


def _vacancy(vacancy_id: str) -> VacancyRow:
    return VacancyRow(
        id=vacancy_id,
        source_post_key=f"ch-{vacancy_id}",
        source_channel="ch",
        source_url="u",
        raw_text=vacancy_id,
        cleaned_text=vacancy_id,
    )


def _seed(session, vacancies: int = 1) -> tuple[int, int, list[str]]:
    user_id = new_user(session)
    resume = ResumeRow(user_id=user_id, name="CV", text="python")
    session.add(resume)
    ids = [f"v-{i}" for i in range(vacancies)]
    session.add_all(_vacancy(v) for v in ids)
    session.flush()
    return user_id, resume.id, ids


def _job(user_id, resume_id, vacancy_id, *, status="queued", kind="match", **extra) -> LlmJobRow:
    return LlmJobRow(user_id=user_id, kind=kind, resume_id=resume_id, vacancy_id=vacancy_id, status=status, **extra)


def test_unique_index_allows_one_active_job_per_pair(db_session):
    user_id, resume_id, (vid,) = _seed(db_session)
    db_session.add_all(
        [
            _job(user_id, resume_id, vid, status="done"),
            _job(user_id, resume_id, vid, status="error"),
            _job(user_id, resume_id, vid),
            _job(user_id, resume_id, vid, kind="cover_letter"),
            # импорт без пары: активных может быть несколько
            _job(user_id, None, None, kind="import"),
            _job(user_id, None, None, kind="import"),
        ]
    )
    db_session.flush()

    with pytest.raises(IntegrityError, match="uq_llm_jobs_one_active_pair"), db_session.begin_nested():
        db_session.add(_job(user_id, resume_id, vid, status="running"))
        db_session.flush()


def _run_parallel(n: int, fn) -> list:
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i: int) -> None:
        barrier.wait(timeout=10)
        try:
            results[i] = fn(i)
        except Exception as exc:  # результат проверяет тест
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


@pytest.fixture
def no_dispatch(monkeypatch):
    """Без реального запуска джоб; окно «проверил — вставил» расширено, чтобы гонка воспроизводилась."""
    monkeypatch.setattr(llm_job_service.LlmJobRunner, "_dispatch", lambda self, *a, **kw: None)
    original = repo.count_active_llm_jobs

    def slow_count(session, **kw):
        n = original(session, **kw)
        time.sleep(0.2)
        return n

    monkeypatch.setattr(repo, "count_active_llm_jobs", slow_count)


def _seed_committed(vacancies: int) -> tuple[int, int, list[str]]:
    with session_scope() as session:
        return _seed(session, vacancies)


def _active_jobs(user_id: int) -> int:
    with session_scope() as session:
        return session.scalar(
            select(func.count())
            .select_from(LlmJobRow)
            .where(LlmJobRow.user_id == user_id, LlmJobRow.status.in_(("queued", "running")))
        )


def test_parallel_enqueue_of_same_pair_creates_one_job(pg_db, no_dispatch):
    user_id, resume_id, (vid,) = _seed_committed(1)

    results = _run_parallel(
        6,
        lambda _i: llm_job_runner.enqueue(user_id=user_id, kind="match", resume_id=resume_id, vacancy_id=vid),
    )

    assert not [r for r in results if isinstance(r, Exception)], results
    assert len({r.job_id for r in results}) == 1
    assert sum(not r.reused for r in results) == 1
    assert _active_jobs(user_id) == 1


def test_parallel_enqueue_respects_per_user_limit(pg_db, no_dispatch):
    # interactive_per_user_limit по умолчанию — 2
    user_id, resume_id, vids = _seed_committed(6)

    results = _run_parallel(
        6,
        lambda i: llm_job_runner.enqueue(user_id=user_id, kind="match", resume_id=resume_id, vacancy_id=vids[i]),
    )

    created = [r for r in results if not isinstance(r, Exception)]
    rejected = [r for r in results if isinstance(r, LlmJobUserLimitError)]
    assert len(created) == 2 and len(rejected) == 4, results
    assert _active_jobs(user_id) == 2


def test_migration_closes_existing_duplicates(pg_empty_db):
    cfg = Config(str(alembic_ini_path()))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "027_vacancy_sightings")

    engine = create_engine(pg_empty_db)
    running_id, queued_id, other_pair_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with sessionmaker(bind=engine)() as s:
        user = UserRow(email="u@example.com", password_hash="x")
        s.add(user)
        s.flush()
        resume = ResumeRow(user_id=user.id, name="CV", text="python")
        s.add_all([resume, _vacancy("v-0"), _vacancy("v-1")])
        s.flush()
        # running старше queued: остаётся именно выполняющаяся
        s.add(_job(user.id, resume.id, "v-0", status="running", id=running_id))
        s.flush()
        s.add(_job(user.id, resume.id, "v-0", id=queued_id))
        s.add(_job(user.id, resume.id, "v-1", id=other_pair_id))
        s.commit()

    command.upgrade(cfg, "head")
    with sessionmaker(bind=engine)() as s:
        got = {r.id: r.status for r in s.scalars(select(LlmJobRow))}
    engine.dispose()
    assert got == {running_id: "running", queued_id: "error", other_pair_id: "queued"}
