"""География и формат работы по ключевым словам."""

from __future__ import annotations

import re

from tg_jobs_parser.extraction.normalizer import normalize_for_match

_COMMON_COUNTRY_MARKERS = {
    "россия": "Россия",
    "russia": "Россия",
    "rf": "Россия",
    "казахстан": "Казахстан",
    "belarus": "Беларусь",
    "беларусь": "Беларусь",
    "украина": "Украина",
    "ukraine": "Украина",
    "германия": "Германия",
    "germany": "Германия",
    "кипр": "Кипр",
    "cyprus": "Кипр",
    "сша": "США",
    "usa": "США",
    "united states": "США",
    "польша": "Польша",
    "poland": "Польша",
    "испания": "Испания",
    "spain": "Испания",
    "франция": "Франция",
    "france": "Франция",
}


def detect_work_format(text: str, synonym_map: dict[str, list[str]]) -> str | None:
    """
    Возвращает ``remote``, ``hybrid``, ``office``, ``relocation`` или ``None``.

    Если совпало несколько — приоритет: relocation > remote > hybrid > office.
    """
    nt = normalize_for_match(text)
    hits: list[str] = []
    for fmt, syns in synonym_map.items():
        for s in syns:
            s_norm = normalize_for_match(s)
            if s_norm and s_norm in nt:
                hits.append(fmt)
                break
    priority = ["relocation", "remote", "hybrid", "office"]
    for p in priority:
        if p in hits:
            return p
    return None


def extract_country_city(text: str) -> tuple[str | None, str | None]:
    """Грубое извлечение страны/города по маркерам и шаблонам."""
    nt = normalize_for_match(text)
    country: str | None = None
    for key, canon in _COMMON_COUNTRY_MARKERS.items():
        if re.search(rf"(?<![a-zа-я]){re.escape(key)}(?![a-zа-я])", nt):
            country = canon
            break

    city: str | None = None
    m = re.search(r"\b(?:город|city)\s*[:\-]\s*([a-zа-яё\- ]{2,40})", text, re.I)
    if m:
        city = m.group(1).strip().title()
    # Москва, Berlin — capitalized token in short list
    known_cities = [
        "москва",
        "санкт-петербург",
        "спб",
        "берлин",
        "berlin",
        "лимассол",
        "limassol",
        "никосия",
        "warsaw",
        "варшава",
    ]
    for c in known_cities:
        if re.search(rf"(?<![a-zа-я]){re.escape(c)}(?![a-zа-я])", nt):
            city = c.title()
            break

    return country, city
