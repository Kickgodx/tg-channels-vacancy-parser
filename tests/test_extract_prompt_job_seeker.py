"""Промпт extraction: job-seeker vs hiring."""

from __future__ import annotations

from jobdesk.ai.prompts import SYSTEM_PROMPT_V1, build_messages
from jobdesk.ai.search_context import SearchContext


def _ctx() -> SearchContext:
    return SearchContext(
        target_specializations=[],
        target_work_formats=["any"],
        exclude_keywords=[],
        allowed_specializations=["QA"],
        allowed_work_formats=["remote", "hybrid", "office", "relocation", "unknown"],
        any_specialization=True,
    )


def test_system_prompt_rejects_job_seekers():
    text = SYSTEM_PROMPT_V1.casefold()
    assert "looking for a" in text or "job seeker" in text or "candidate seeking" in text
    assert "dm me" in text or "for cv" in text or "резюме" in text


def test_few_shot_includes_english_job_seeker():
    msgs = build_messages(
        text="Hiring: Senior QA, remote",
        search_context=_ctx(),
        use_few_shot=True,
    )
    user = msgs[1]["content"]
    assert "Looking for a QA Engineer role" in user
    assert '"is_job_post": false' in user.casefold() or '"is_job_post": false' in user


def test_few_shot_includes_hourly_currency():
    msgs = build_messages(
        text="Hiring: Dev, remote",
        search_context=_ctx(),
        use_few_shot=True,
    )
    user = msgs[1]["content"]
    assert "USD/HOUR" in user
    assert "/ hour" in user or "/hour" in user.casefold()
