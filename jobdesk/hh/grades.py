"""Маппинг HH experience → canonical detected_grade."""

from __future__ import annotations

import re

from jobdesk.extraction.grade_extractor import UNKNOWN_GRADE, normalize_grade
from jobdesk.hh.models import IdName

# Стабильные id из GET /dictionaries → experience.
_EXPERIENCE_ID_TO_GRADE: dict[str, str] = {
    "noExperience": "intern",
    "between1And3": "junior",
    "between3And6": "middle",
    "moreThan6": "senior",
}

# Эвристики для бэкфилла по required_experience (name), когда id уже не хранится.
_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("intern", re.compile(r"нет\s+опыта|без\s+опыта|no\s*experience", re.I)),
    ("senior", re.compile(r"более\s*6|больше\s*6|more\s*than\s*6|6\s*\+", re.I)),
    ("middle", re.compile(r"от\s*3\s*до\s*6|3\s*[–\-]\s*6|between\s*3\s*and\s*6", re.I)),
    ("junior", re.compile(r"от\s*1\s*(года\s*)?до\s*3|1\s*[–\-]\s*3|between\s*1\s*and\s*3", re.I)),
]


def grade_from_experience(experience: IdName | None) -> str:
    """Грейд по ``experience.id``; иначе ``unknown``."""
    if experience is None or not experience.id:
        return UNKNOWN_GRADE
    mapped = _EXPERIENCE_ID_TO_GRADE.get(experience.id.strip())
    return normalize_grade(mapped) if mapped else UNKNOWN_GRADE


def grade_from_experience_name(name: str | None) -> str:
    """Грейд по человекочитаемому ``experience.name`` (бэкфилл старых строк)."""
    if not name or not str(name).strip():
        return UNKNOWN_GRADE
    text = str(name).strip()
    for grade, rx in _NAME_PATTERNS:
        if rx.search(text):
            return normalize_grade(grade)
    return UNKNOWN_GRADE
