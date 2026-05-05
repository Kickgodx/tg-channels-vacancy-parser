"""Извлечение грейда из текста."""

from __future__ import annotations

import re

from tg_jobs_parser.extraction.normalizer import normalize_for_match

_ORDER = [
    "intern",
    "junior",
    "junior+",
    "middle",
    "middle+",
    "senior",
    "senior+",
    "lead",
    "staff",
    "principal",
]

_RANK = {name: i for i, name in enumerate(_ORDER)}
GRADE_RANK = _RANK

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("intern", re.compile(r"\b(intern|trainee|стаж|интерн)\b", re.I)),
    ("junior+", re.compile(r"\b(junior\+|джуниор\+|\bj\+\b)\b", re.I)),
    ("junior", re.compile(r"\b(junior|джун|джуниор)\b", re.I)),
    ("middle+", re.compile(r"\b(middle\+|мидл\+|\bm\+\b)\b", re.I)),
    ("middle", re.compile(r"\b(middle|mid|мидл|middl)\b", re.I)),
    ("senior+", re.compile(r"\b(senior\+|сеньор\+|синьор\+)\b", re.I)),
    ("senior", re.compile(r"\b(senior|сеньор|синьор|\bsr\.?\b)\b", re.I)),
    ("lead", re.compile(r"\b(lead|лид|team\s*lead|tech\s*lead)\b", re.I)),
    ("staff", re.compile(r"\b(staff\s+engineer|staff)\b", re.I)),
    ("principal", re.compile(r"\b(principal\s+engineer|principal)\b", re.I)),
]


UNKNOWN_GRADE = "unknown"


def extract_grade(text: str) -> str:
    """Возвращает грейд с наибольшим рангом из найденных; иначе ``unknown``."""
    nt = normalize_for_match(text)
    best: str | None = None
    best_rank = -1
    for grade, rx in _PATTERNS:
        if rx.search(text) or rx.search(nt):
            rnk = _RANK.get(grade, -1)
            if rnk > best_rank:
                best_rank = rnk
                best = grade
    return best if best is not None else UNKNOWN_GRADE
