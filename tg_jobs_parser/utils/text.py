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


def post_id_display(full_id: str) -> str:
    """
    ``qa_jobs-255541`` / ``qa-jobs-255541`` → ``255541`` (числовой id сообщения в Telegram).

    Если суффикс не только цифры — возвращает исходную строку.
    """
    if not full_id:
        return ""
    tail = full_id.rsplit("-", 1)[-1]
    return tail if tail.isdigit() else full_id
