"""Стадии ingest после rules: optional AI + policy gates."""

from __future__ import annotations

from typing import TYPE_CHECKING

from jobdesk.config.schema import AppConfig
from jobdesk.filters.vacancy_filters import apply_post_filters
from jobdesk.ingestion.policy import IngestPolicy
from jobdesk.ingestion.rules import rules_from_raw_post
from jobdesk.models.vacancy import Vacancy
from jobdesk.parse_cancel import ParseCancellationToken
from jobdesk.parsers.base import RawPost

if TYPE_CHECKING:
    from jobdesk.ai.enricher import AIEnrichmentService


def apply_ingest_stages(
    vacancy: Vacancy,
    config: AppConfig,
    *,
    policy: IngestPolicy,
    ai_service: AIEnrichmentService | None = None,
    exclude_keywords: list[str] | None = None,
    cancel: ParseCancellationToken | None = None,
) -> Vacancy | None:
    """AI (если есть) + порог без AI + опциональные post-filters → Vacancy | None."""
    v = vacancy
    if ai_service is not None:
        if cancel and cancel.is_cancelled():
            return None
        v = ai_service.enrich(v, cancel=cancel)
        if v is None:
            return None
    elif policy.score_threshold_without_ai is not None:
        if v.vacancy_score < policy.score_threshold_without_ai:
            return None

    if policy.apply_post_filters and not apply_post_filters(v, config, exclude_keywords=exclude_keywords):
        return None
    return v


def process_raw_post(
    post: RawPost,
    config: AppConfig,
    *,
    policy: IngestPolicy,
    ai_service: AIEnrichmentService | None = None,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
    exclude_keywords: list[str] | None = None,
    cancel: ParseCancellationToken | None = None,
) -> Vacancy | None:
    """RawPost → rules → AI/gates → Vacancy | None."""
    if cancel and cancel.is_cancelled():
        return None
    v = rules_from_raw_post(
        post,
        config,
        spec_map=spec_map,
        tech_list=tech_list,
        wf_map=wf_map,
    )
    return apply_ingest_stages(
        v,
        config,
        policy=policy,
        ai_service=ai_service,
        exclude_keywords=exclude_keywords,
        cancel=cancel,
    )
