"""Хэши текста."""

from __future__ import annotations

import hashlib


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def short_id_from_hash(hex_digest: str, n: int = 12) -> str:
    return hex_digest[:n]
