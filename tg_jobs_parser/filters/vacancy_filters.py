"""Фильтрация вакансий по правилам из конфигурации."""

from __future__ import annotations

import re

from tg_jobs_parser.config.schema import AppConfig
from tg_jobs_parser.extraction.normalizer import normalize_for_match
from tg_jobs_parser.models.vacancy import Vacancy


def matches_specialization_filter(v: Vacancy, selected: list[str]) -> bool:
    """Если ``selected`` пуст — разрешаем все специализации из словаря."""
    if not selected:
        return True
    selected_set = set(selected)
    detected = set(v.detected_specializations)
    return bool(detected & selected_set)


def matches_exclude_keywords(v: Vacancy, keywords: list[str]) -> bool:
    """True если пост **не** содержит исключающих ключей (т.е. проходит фильтр)."""
    nt = normalize_for_match(v.cleaned_text)
    for kw in keywords:
        k = normalize_for_match(kw)
        if not k:
            continue
        if len(k) <= 2:
            if re.search(rf"(?<![a-zа-я0-9ё]){re.escape(k)}(?![a-zа-я0-9ё])", nt):
                return False
        elif k in nt:
            return False
    return True


def matches_work_format_policy(v: Vacancy, formats: list[str]) -> bool:
    """
    ``formats`` элементы: ``remote``, ``hybrid``, ``office``, ``relocation``, ``any``, ``exclude_office``.
    """
    if not formats or "any" in formats:
        return True

    exclude_office = "exclude_office" in formats
    allowed = {f for f in formats if f not in ("exclude_office", "any")}

    if exclude_office and v.work_format == "office":
        return False

    if not allowed:
        return v.work_format != "office" if exclude_office else True

    if v.work_format is None:
        return True

    return v.work_format in allowed


def matches_location_policy(
    v: Vacancy,
    countries: list[str],
    cities: list[str],
) -> bool:
    """Пустые списки — без ограничения. Страна / город ``None`` или пустая строка — «неизвестно», пропускаем."""
    nt = normalize_for_match(v.cleaned_text)
    if countries:
        c_norms = [normalize_for_match(c) for c in countries]
        vc = normalize_for_match(v.country or "")
        matched = vc and any(c in vc or vc in c for c in c_norms)
        text_country = any(c in nt for c in c_norms)
        if not (matched or text_country or not vc):
            return False

    if cities:
        ct_norms = [normalize_for_match(c) for c in cities]
        city_v = normalize_for_match(v.city or "")
        matched_city = city_v and any(c in city_v or city_v in c for c in ct_norms)
        text_city = any(c in nt for c in ct_norms)
        if not (matched_city or text_city or not city_v):
            return False

    return True


def apply_post_filters(
    v: Vacancy,
    config: AppConfig,
    *,
    exclude_keywords: list[str] | None = None,
) -> bool:
    """
    Возвращает True если вакансия остаётся в выдаче.

    ``exclude_keywords`` можно передать заранее, чтобы не пересобирать список на каждом посте
    (актуально при пакетной обработке).
    """
    if v.vacancy_score < config.parser.vacancy_score_threshold:
        return False
    if v.vacancy_substantive_score < config.parser.vacancy_min_substantive_score:
        return False
    if not matches_specialization_filter(v, config.filters.specializations):
        return False
    excl = exclude_keywords if exclude_keywords is not None else config.merged_exclude_keywords()
    if not matches_exclude_keywords(v, excl):
        return False
    if not matches_work_format_policy(v, config.filters.work_formats):
        return False
    return matches_location_policy(v, config.filters.countries, config.filters.cities)
