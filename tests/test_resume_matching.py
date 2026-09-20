"""Матчинг резюме: CRUD резюме, vacancy_matches, join в list_vacancies, промпт."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db import repository as repo
from backend.db.base import Base
from backend.db.models import VacancyRow
from backend.schemas import VacancyQuery
from tg_jobs_parser.ai.clients.openrouter import _parse_match
from tg_jobs_parser.ai.prompts import MATCH_PROMPT_VERSION, build_match_messages


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 't.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _vacancy(vid: str, text: str, score: int = 0) -> VacancyRow:
    return VacancyRow(
        id=vid,
        source_post_key=f"ch-{vid}",
        source_channel="ch",
        source_url="https://t.me/s/ch",
        post_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        raw_text=text,
        cleaned_text=text,
        vacancy_score=score,
    )


def test_resumes_crud(db_session):
    assert repo.list_resumes(db_session) == []

    r = repo.create_resume(db_session, name="Python dev", text="5 лет Python, Django, FastAPI")
    db_session.commit()

    rows = repo.list_resumes(db_session)
    assert [x.id for x in rows] == [r.id]
    assert rows[0].name == "Python dev"

    assert repo.delete_resume(db_session, r.id) is True
    assert repo.delete_resume(db_session, r.id) is False
    assert repo.list_resumes(db_session) == []


def test_match_upsert_and_join_in_list_vacancies(db_session):
    resume = repo.create_resume(db_session, name="r", text="python")
    db_session.add_all(
        [_vacancy("a", "Python dev", 10), _vacancy("b", "Java dev", 5), _vacancy("c", "Go dev", 1)]
    )
    db_session.commit()

    repo.upsert_vacancy_match(
        db_session, resume_id=resume.id, vacancy_id="a", score=88,
        explanation="Сильное совпадение", matching_skills=["python"],
        missing_skills=["k8s"], model="m1",
    )
    repo.upsert_vacancy_match(
        db_session, resume_id=resume.id, vacancy_id="c", score=40,
        explanation="Слабое", matching_skills=[], missing_skills=["go"], model="m1",
    )
    db_session.commit()

    items, total = repo.list_vacancies(
        db_session, VacancyQuery(resume_id=resume.id, sort_by="match_score")
    )
    assert total == 3
    assert [v.id for v in items] == ["a", "c", "b"]  # nulls last
    assert items[0].match_score == 88
    assert items[0].match_explanation == "Сильное совпадение"
    assert items[2].match_score is None

    # upsert обновляет существующую пару
    repo.upsert_vacancy_match(
        db_session, resume_id=resume.id, vacancy_id="a", score=55,
        explanation="Переоценено", matching_skills=[], missing_skills=[], model="m2",
    )
    db_session.commit()
    items, _ = repo.list_vacancies(db_session, VacancyQuery(resume_id=resume.id))
    assert next(v for v in items if v.id == "a").match_score == 55


def test_match_prompt_and_parse():
    assert MATCH_PROMPT_VERSION
    messages = build_match_messages(resume_text="Python, FastAPI", vacancy_text="Нужен Python-разработчик")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Python, FastAPI" in messages[1]["content"]
    assert "Нужен Python-разработчик" in messages[1]["content"]

    m = _parse_match('{"score": 85, "explanation": "Ок", "matching_skills": ["python"], "missing_skills": ["k8s"]}')
    assert m.score == 85
    assert m.matching_skills == ["python"]

    fenced = _parse_match('```json\n{"score": 30}\n```')
    assert fenced.score == 30
    assert fenced.matching_skills == []

    with pytest.raises(Exception):
        _parse_match("not json")
