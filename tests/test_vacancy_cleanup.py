"""Разовая чистка: посты соискателей и подборки удаляются, технологии переписываются."""

from __future__ import annotations

from datetime import UTC, datetime

from typer.testing import CliRunner

from backend.db.models import VacancyRow
from backend.db.session import session_scope
from backend.services.vacancy_cleanup import apply_cleanup, plan_cleanup
from jobdesk.cli.db import db_app


def _vac(vid: str, text: str, technologies: list[str], **kw) -> VacancyRow:
    return VacancyRow(
        id=vid,
        source_post_key=f"k-{vid}",
        source_channel="ch",
        source_url="https://t.me/ch",
        post_date=datetime(2026, 10, 1, tzinfo=UTC),
        raw_text=text,
        cleaned_text=text,
        technologies=technologies,
        **kw,
    )


def _seed() -> None:
    with session_scope() as s:
        s.add_all(
            [
                _vac("digest", "Подборка вакансий на 29.10.2026 Backend Go Developer", ["Go"]),
                _vac("digest-copy", "Подборка вакансий на 29.10.2026 Backend Go Developer", ["Go"], is_duplicate=True),
                _vac("seeker", "#resume #frontend React developer, open to offers", ["React"]),
                _vac("dirty", "Тестировщик, присылайте резюме", ["Функциональное тестирование", "Python 3.12, Pytest"]),
                _vac("clean", "Требуется Go-разработчик", ["Go", "Kafka"]),
                _vac("cv-vision", "Senior CV/ML Engineer #вакансия #CV", ["Python"]),
            ]
        )


def _state() -> dict[str, list[str]]:
    with session_scope() as s:
        return {vid: list(t) for vid, t in s.query(VacancyRow.id, VacancyRow.technologies).all()}


def test_plan_and_apply(pg_db):
    _seed()
    with session_scope() as s:
        plan = plan_cleanup(s)
        assert sorted(plan.delete_ids) == ["digest", "digest-copy", "seeker"]
        assert plan.technology_updates == {"dirty": ["Python", "Pytest"]}
        assert plan.removed["Функциональное тестирование"] == 1
        assert apply_cleanup(s, plan) == (3, 1)
    assert _state() == {"dirty": ["Python", "Pytest"], "clean": ["Go", "Kafka"], "cv-vision": ["Python"]}
    # Повторный прогон — уже нечего делать.
    with session_scope() as s:
        again = plan_cleanup(s)
    assert (again.delete_ids, again.technology_updates) == ([], {})


def test_cli_dry_run_changes_nothing_and_apply_does(pg_db):
    _seed()
    before = _state()
    runner = CliRunner()
    dry = runner.invoke(db_app, ["clean-vacancies"])
    assert dry.exit_code == 0, dry.output
    assert "удалить: 3" in dry.output and "--apply" in dry.output
    assert _state() == before
    applied = runner.invoke(db_app, ["clean-vacancies", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "Удалено: 3" in applied.output
    assert set(_state()) == {"dirty", "clean", "cv-vision"}
