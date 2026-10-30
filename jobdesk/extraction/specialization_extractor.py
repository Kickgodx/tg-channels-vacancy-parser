"""Определение специализаций по синонимам из конфигурации."""

from __future__ import annotations

import re

from jobdesk.extraction.normalizer import normalize_for_match


def extract_specializations(text: str, spec_map: dict[str, list[str]]) -> list[str]:
    """
    Возвращает канонические названия специализаций, найденные в тексте.

    ``spec_map``: canonical_name -> list of lowercase synonyms.
    """
    normalized = normalize_for_match(text)
    found: list[str] = []
    for canonical, syns in spec_map.items():
        candidates = [normalize_for_match(s) for s in syns] + [normalize_for_match(canonical)]
        for c in candidates:
            if not c:
                continue
            if len(c) <= 2:
                # короткие токены — по границам слова
                if re.search(rf"(?<![a-z0-9]){re.escape(c)}(?![a-z0-9])", normalized):
                    found.append(canonical)
                    break
            elif c in normalized:
                found.append(canonical)
                break
    return list(dict.fromkeys(found))
