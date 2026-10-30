"""Хэши текста."""

from __future__ import annotations

import hashlib

from jobdesk.extraction.normalizer import normalize_for_match


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def short_id_from_hash(hex_digest: str, n: int = 12) -> str:
    return hex_digest[:n]


def vacancy_content_id(cleaned_text: str) -> str:
    """Канонический id вакансии: sha256 нормализованного текста."""
    return sha256_text(normalize_for_match(cleaned_text))
