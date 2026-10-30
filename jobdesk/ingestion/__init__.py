"""Общий путь ingest: RawPost → rules → optional AI → policy gates."""

from jobdesk.ingestion.policy import IngestPolicy
from jobdesk.ingestion.process import apply_ingest_stages, process_raw_post
from jobdesk.ingestion.rules import (
    raw_post_to_vacancy,
    raw_post_to_vacancy_rules,
    rules_from_raw_post,
)

__all__ = [
    "IngestPolicy",
    "apply_ingest_stages",
    "process_raw_post",
    "raw_post_to_vacancy",
    "raw_post_to_vacancy_rules",
    "rules_from_raw_post",
]
