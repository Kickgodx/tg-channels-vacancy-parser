"""Поиск технологий из расширяемого словаря."""

from __future__ import annotations

import re

from tg_jobs_parser.extraction.normalizer import normalize_for_match


def extract_technologies(text: str, tech_list: list[str]) -> list[str]:
    """Совпадение по имени технологии (для коротких имён — границы слова)."""
    nt = normalize_for_match(text)
    found: list[str] = []
    for tech in sorted({t for t in tech_list}, key=len, reverse=True):
        tn = normalize_for_match(tech)
        if not tn:
            continue
        if len(tn) <= 2:
            if re.search(rf"(?<![a-z0-9]){re.escape(tn)}(?![a-z0-9])", nt):
                found.append(tech)
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(tn)}(?![a-z0-9])", nt):
            found.append(tech)
    return list(dict.fromkeys(found))
