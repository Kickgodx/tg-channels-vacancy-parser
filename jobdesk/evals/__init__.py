"""Golden-set evals для LLM extraction (offline scorer + optional live run)."""

from __future__ import annotations

from jobdesk.evals.score_extraction import (
    aggregate_scores,
    score_extraction,
)

__all__ = ["aggregate_scores", "score_extraction"]
