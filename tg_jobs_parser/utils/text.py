"""Текстовые утилиты."""

from __future__ import annotations

import re


def compact_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def split_lines(s: str) -> list[str]:
    return [ln.strip() for ln in s.splitlines() if ln.strip()]


def first_sentence(text: str, max_len: int = 200) -> str:
    """Первые символы / «заголовок» для fuzzy-сравнения."""
    t = compact_whitespace(text)
    return t[:max_len]
