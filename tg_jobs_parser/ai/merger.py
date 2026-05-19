"""Слияние rule-based Vacancy и ответа LLM."""

from __future__ import annotations

import re

from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.config.schema import AiConfig, AppConfig
from tg_jobs_parser.models.vacancy import Vacancy

_URL_RE = re.compile(r"https?://[^\s\]\)\"'<>]+", re.I)

_SALARY_FIELDS = frozenset(
    {"detected_salary_min", "detected_salary_max", "salary_currency", "detected_salary_avg"}
)


def _normalize_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = u.strip().rstrip(".,;)")
        if not u or u in seen:
            continue
        if not _URL_RE.match(u):
            continue
        seen.add(u)
        out.append(u)
    return out


def _merge_specs(
    rules: list[str],
    ai: list[str],
    *,
    allow_unknown: bool,
    allowed: set[str],
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for s in list(rules) + list(ai):
        name = s.strip()
        if not name or name in seen:
            continue
        if name in allowed or allow_unknown:
            seen.add(name)
            merged.append(name)
    return merged


def merge_rules_and_ai(
    vacancy: Vacancy,
    ai: VacancyAIExtraction,
    config: AppConfig,
) -> Vacancy:
    """Применить поля LLM к вакансии согласно ai.mode и ai.merge."""
    ai_cfg = config.ai
    merge_cfg = ai_cfg.merge
    conf = ai.confidence
    prefer = set(merge_cfg.prefer_ai_for)
    allowed_specs = set(config.merged_specializations().keys())

    updates: dict = {}
    source = "hybrid" if ai_cfg.mode == "assist" else "ai"

    def _use_ai(field: str) -> bool:
        if ai_cfg.mode == "primary":
            return True
        return field in prefer and conf >= merge_cfg.min_confidence

    # primary: только роли от LLM (rules часто дают ложные QA/аналитик по подстрокам)
    rules_specs = [] if ai_cfg.mode == "primary" else vacancy.detected_specializations
    specs = _merge_specs(
        rules_specs,
        ai.detected_specializations,
        allow_unknown=ai_cfg.allow_unknown_specializations,
        allowed=allowed_specs,
    )
    if _use_ai("detected_specializations") or ai_cfg.mode == "primary":
        updates["detected_specializations"] = specs
    elif specs:
        updates["detected_specializations"] = specs

    if ai_cfg.mode == "primary":
        # В primary грейд только от LLM; rules не подставляем при unknown.
        updates["detected_grade"] = ai.detected_grade
    elif _use_ai("detected_grade") and ai.detected_grade != "unknown":
        updates["detected_grade"] = ai.detected_grade

    for fld, ai_val, rules_val in (
        ("detected_salary_min", ai.salary_min, vacancy.detected_salary_min),
        ("detected_salary_max", ai.salary_max, vacancy.detected_salary_max),
        ("salary_currency", ai.salary_currency, vacancy.salary_currency),
        ("work_format", ai.work_format, vacancy.work_format),
        ("country", ai.country, vacancy.country),
        ("city", ai.city, vacancy.city),
        ("company", ai.company, vacancy.company),
        ("contact", ai.contact, vacancy.contact),
    ):
        if ai_cfg.mode == "primary" and fld in _SALARY_FIELDS:
            updates[fld] = ai_val
            continue
        if _use_ai(fld) and ai_val is not None:
            updates[fld] = ai_val
        elif rules_val is None and ai_val is not None:
            updates[fld] = ai_val

    ai_urls = _normalize_urls(ai.application_urls)
    rules_urls = _normalize_urls(vacancy.application_urls)
    if _use_ai("application_urls"):
        updates["application_urls"] = ai_urls or rules_urls
    else:
        updates["application_urls"] = rules_urls or ai_urls

    if ai.technologies:
        tech_set = list(dict.fromkeys(vacancy.technologies + ai.technologies))
        updates["technologies"] = tech_set

    if ai.required_experience and (_use_ai("required_experience") or not vacancy.required_experience):
        updates["required_experience"] = ai.required_experience
    if ai.employment_type and (_use_ai("employment_type") or not vacancy.employment_type):
        updates["employment_type"] = ai.employment_type

    smin = updates.get("detected_salary_min", vacancy.detected_salary_min)
    smax = updates.get("detected_salary_max", vacancy.detected_salary_max)
    if smin is None and smax is None:
        updates["detected_salary_avg"] = None
    elif smin is not None and smax is not None:
        updates["detected_salary_avg"] = (float(smin) + float(smax)) / 2
    elif smin is not None:
        updates["detected_salary_avg"] = float(smin)
    elif smax is not None:
        updates["detected_salary_avg"] = float(smax)

    updates["extraction_source"] = source
    updates["ai_confidence"] = conf

    return vacancy.model_copy(update=updates)
