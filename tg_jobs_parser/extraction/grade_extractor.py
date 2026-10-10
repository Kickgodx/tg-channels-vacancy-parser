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
    # Не «to staff teams» — только уровень вроде Staff Engineer / staff-level
    ("staff", re.compile(r"\bstaff(?:\s+|-)(?:engineer|level)\b", re.I)),
    ("principal", re.compile(r"\b(principal\s+engineer|principal)\b", re.I)),
]


UNKNOWN_GRADE = "unknown"

GRADE_OPTIONS: tuple[str, ...] = (*tuple(_ORDER), UNKNOWN_GRADE)
_GRADE_SET = frozenset(GRADE_OPTIONS)

_ALIASES: dict[str, str] = {
    "jr": "junior",
    "jr.": "junior",
    "sr": "senior",
    "sr.": "senior",
    "mid": "middle",
    "trainee": "intern",
    "стажер": "intern",
    "стажёр": "intern",
}


def normalize_grade(value: object) -> str:
    """Привести грейд к каноническому значению из GRADE_OPTIONS."""
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return UNKNOWN_GRADE
    g = str(value).strip().lower().replace(" ", "")
    if g in _ALIASES:
        g = _ALIASES[g]
    if g in _GRADE_SET:
        return g
    return UNKNOWN_GRADE


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
    return normalize_grade(best) if best is not None else UNKNOWN_GRADE
