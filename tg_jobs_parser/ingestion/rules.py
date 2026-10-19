"""Rule-based извлечение Vacancy из RawPost (без LLM)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from tg_jobs_parser.config.schema import AppConfig, split_specializations
from tg_jobs_parser.extraction.company_extractor import extract_company
from tg_jobs_parser.extraction.contact_extractor import extract_contact
from tg_jobs_parser.extraction.grade_extractor import extract_grade
from tg_jobs_parser.extraction.location_extractor import (
    detect_work_format,
    extract_country_city,
)
from tg_jobs_parser.extraction.normalizer import clean_display_text, normalize_for_match
from tg_jobs_parser.extraction.salary_extractor import extract_salary
from tg_jobs_parser.extraction.salary_period import (
    apply_hourly_currency_from_text,
    normalize_annual_salary_to_monthly,
    sanitize_salary_amounts,
    sanitize_salary_currency_period,
)
from tg_jobs_parser.extraction.specialization_extractor import extract_specializations
from tg_jobs_parser.extraction.technology_extractor import extract_technologies
from tg_jobs_parser.extraction.vacancy_detector import compute_vacancy_score
from tg_jobs_parser.models.vacancy import Vacancy
from tg_jobs_parser.parsers.base import RawPost
from tg_jobs_parser.parsers.channel_username import normalize_channel_username
from tg_jobs_parser.utils.hashing import vacancy_content_id


def _extract_employment(text: str) -> str | None:
    t = normalize_for_match(text)
    if "full-time" in t or "фуллтайм" in t or "полная занятость" in t:
        return "full-time"
    if "part-time" in t or "part time" in t or "частичная" in t:
        return "part-time"
    if "contract" in t or "контракт" in t or "контрактор" in t:
        return "contract"
    return None


def _extract_experience(text: str) -> str | None:
    m = re.search(
        r"\b(\d+\+?\s*(?:лет|years?|y\/o)|опыт\s*от\s*\d+[\s\-]*\d*\s*(?:лет|years)?)\b",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()
    if re.search(r"\bno\s+experience\b|без\s+опыт", text, re.I):
        return "no experience"
    return None


def rules_from_raw_post(
    post: RawPost,
    config: AppConfig,
    *,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
) -> Vacancy:
    """
    Rule-based извлечение (без LLM).

    Словари можно передать заранее (``spec_map``/``tech_list``/``wf_map``) — это критично
    при пакетной обработке, иначе на каждый пост они пересобираются заново.
    """
    raw = post.text
    cleaned = clean_display_text(raw)
    score, _, substantive = compute_vacancy_score(raw)

    if spec_map is None:
        spec_map = config.merged_specializations()
    if tech_list is None:
        tech_list = config.merged_technologies()
    if wf_map is None:
        wf_map = config.merged_work_format_synonyms()

    specs = extract_specializations(raw, spec_map)
    salary = extract_salary(raw)
    grade = extract_grade(raw)
    country, city = extract_country_city(raw)
    wf = detect_work_format(raw, wf_map)
    contact = extract_contact(raw)
    # Языки из пользовательского словаря специализаций — тоже в технологии.
    specs, technologies = split_specializations(specs, extract_technologies(raw, tech_list))
    company = extract_company(raw)

    ch = normalize_channel_username(post.channel_username)
    source_post_key = f"{ch}-{post.post_id}"
    vid = vacancy_content_id(cleaned)

    source_url = f"https://t.me/s/{ch}"

    avg = salary.avg_value
    if salary.min_value is not None and salary.max_value is not None:
        avg = (salary.min_value + salary.max_value) / 2

    vacancy = Vacancy(
        id=vid,
        source_post_key=source_post_key,
        source_channel=ch,
        source_url=source_url,
        post_url=post.post_url,
        post_date=post.post_date,
        raw_text=raw,
        cleaned_text=cleaned,
        detected_specializations=specs,
        detected_grade=grade,
        detected_salary_min=salary.min_value,
        detected_salary_max=salary.max_value,
        detected_salary_avg=avg,
        salary_currency=salary.currency,
        work_format=wf,
        country=country,
        city=city,
        company=company,
        contact=contact,
        technologies=technologies,
        required_experience=_extract_experience(raw),
        employment_type=_extract_employment(raw),
        vacancy_score=score,
        vacancy_substantive_score=substantive,
        is_duplicate=False,
        duplicate_group_id=None,
        duplicate_reason=None,
        parsed_at=datetime.now(UTC),
    )
    apply_hourly_currency_from_text(vacancy)
    sanitize_salary_currency_period(vacancy)
    normalize_annual_salary_to_monthly(vacancy)
    sanitize_salary_amounts(vacancy, default_currency_if_missing=config.fx.default_currency_if_missing)
    return vacancy


def raw_post_to_vacancy_rules(
    post: RawPost,
    config: AppConfig,
    *,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
) -> Vacancy:
    """Алиас ``rules_from_raw_post`` (обратная совместимость)."""
    return rules_from_raw_post(post, config, spec_map=spec_map, tech_list=tech_list, wf_map=wf_map)


def raw_post_to_vacancy(
    post: RawPost,
    config: AppConfig,
    *,
    spec_map: dict[str, list[str]] | None = None,
    tech_list: list[str] | None = None,
    wf_map: dict[str, list[str]] | None = None,
) -> Vacancy:
    """Алиас rule-based извлечения (обратная совместимость)."""
    return rules_from_raw_post(post, config, spec_map=spec_map, tech_list=tech_list, wf_map=wf_map)
