"""Shared ingestion: rules → AI/gates → Vacancy | None."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from jobdesk.config.schema import AppConfig, FiltersConfig, ParserConfig, SourcesConfig
from jobdesk.ingestion.policy import MANUAL_POLICY, IngestPolicy
from jobdesk.ingestion.process import apply_ingest_stages, process_raw_post
from jobdesk.ingestion.rules import rules_from_raw_post
from jobdesk.models.vacancy import Vacancy
from jobdesk.parsers.base import RawPost


def _cfg(**kwargs) -> AppConfig:
    data = {
        "sources": SourcesConfig(channels=["ch"]),
        "filters": FiltersConfig(specializations=[], work_formats=["any"]),
        "parser": ParserConfig(vacancy_score_threshold=30, vacancy_min_substantive_score=0),
    }
    data.update(kwargs)
    return AppConfig(**data)


def _post(text: str, *, channel: str = "jobs") -> RawPost:
    return RawPost(
        channel_username=channel,
        post_id="1",
        text=text,
        post_date=datetime.now(UTC),
        post_url=None,
    )


VACANCY_TEXT = "Вакансия: Python-разработчик. Remote. Зарплата 200000 руб. Нужен опыт PostgreSQL. Полная занятость."


def test_rules_from_raw_post_sets_channel_and_score():
    cfg = _cfg()
    v = rules_from_raw_post(_post(VACANCY_TEXT), cfg)
    assert v.source_channel == "jobs"
    assert v.vacancy_score >= 0
    assert "Python" in v.detected_specializations or v.vacancy_score > 0


def test_process_raw_post_rules_only_drops_low_score():
    cfg = _cfg()
    policy = IngestPolicy(apply_post_filters=False, score_threshold_without_ai=90)
    out = process_raw_post(_post("привет"), cfg, policy=policy)
    assert out is None


def test_process_raw_post_telegram_filters_exclude_keyword():
    cfg = _cfg(
        filters=FiltersConfig(
            specializations=[],
            work_formats=["any"],
            exclude_keywords=["спам"],
        )
    )
    policy = IngestPolicy(apply_post_filters=True, score_threshold_without_ai=None)
    # Высокий score через длинный vacancy-like текст, но exclude
    text = VACANCY_TEXT + " спам-слово"
    # exclude is "спам" which matches in text
    out = process_raw_post(_post(text), cfg, policy=policy)
    assert out is None


def test_manual_policy_skips_post_filters():
    cfg = _cfg(
        filters=FiltersConfig(
            specializations=["Java"],
            work_formats=["office"],
            exclude_keywords=["python"],
        )
    )
    v = rules_from_raw_post(_post(VACANCY_TEXT), cfg)
    out = apply_ingest_stages(v, cfg, policy=MANUAL_POLICY)
    assert out is not None
    assert out.id == v.id


def test_apply_ingest_stages_ai_reject():
    cfg = _cfg()
    v = Vacancy(
        id="x",
        source_post_key="manual-1",
        source_channel="manual",
        source_url="",
        post_url=None,
        post_date=datetime.now(UTC),
        raw_text="t",
        cleaned_text="t",
        vacancy_score=50,
        vacancy_substantive_score=10,
    )
    ai = MagicMock()
    ai.enrich.return_value = None
    out = apply_ingest_stages(v, cfg, policy=MANUAL_POLICY, ai_service=ai)
    assert out is None
    ai.enrich.assert_called_once()
