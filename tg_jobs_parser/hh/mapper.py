"""Маппинг HH VacancyDetail / VacancyShort → модель Vacancy (без LLM)."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tg_jobs_parser.hh.models import (
    Contacts,
    Salary,
    SalaryRange,
    VacancyDetail,
    VacancyShort,
)
from tg_jobs_parser.models.vacancy import Vacancy

HH_CHANNEL = "hh"
HH_SOURCE_URL = "https://hh.ru"
EXTRACTION_SOURCE = "hh_api"
VACANCY_SCORE = 100

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Служебные query от HH API / виджетов — не нужны в «Открыть пост».
_HH_TRACKING_PARAMS = frozenset({"tech_source", "hhtmfrom", "hhtmfromlabel", "from"})

_WORK_FORMAT_MAP = {
    "REMOTE": "remote",
    "HYBRID": "hybrid",
    "ON_SITE": "office",
    "FIELD_WORK": "office",
}


def hh_source_post_key(hh_id: str) -> str:
    return f"hh-{hh_id}"


def stable_vacancy_id(hh_id: str) -> str:
    return hashlib.sha256(f"hh:{hh_id}".encode()).hexdigest()


def clean_hh_url(url: str | None) -> str | None:
    """Убирает tracking-query HH (``tech_source=api`` и т.п.)."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.query:
        return url.strip()
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _HH_TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept, doseq=True), parts.fragment)) or None


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def parse_hh_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    # HH часто отдаёт +0300 без двоеточия
    if len(raw) >= 5 and (raw[-5] in "+-") and raw[-3] != ":":
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def map_work_format(items: list) -> str | None:
    mapped: list[str] = []
    for item in items or []:
        key = (getattr(item, "id", None) or "").upper()
        fmt = _WORK_FORMAT_MAP.get(key)
        if fmt and fmt not in mapped:
            mapped.append(fmt)
    if not mapped:
        return None
    priority = ["remote", "hybrid", "office"]
    for p in priority:
        if p in mapped:
            return p
    return mapped[0]


def _salary_fields(
    salary: Salary | SalaryRange | None,
) -> tuple[float | None, float | None, float | None, str | None]:
    if salary is None:
        return None, None, None, None
    lo = float(salary.from_) if salary.from_ is not None else None
    hi = float(salary.to) if salary.to is not None else None
    avg = None
    if lo is not None and hi is not None:
        avg = (lo + hi) / 2.0
    elif lo is not None:
        avg = lo
    elif hi is not None:
        avg = hi
    currency = salary.currency
    if currency == "RUR":
        currency = "RUB"
    return lo, hi, avg, currency


def _contact_from(contacts: Contacts | None) -> str | None:
    if contacts is None:
        return None
    parts: list[str] = []
    if contacts.name:
        parts.append(contacts.name)
    if contacts.email:
        parts.append(contacts.email)
    for phone in contacts.phones or []:
        if phone.formatted:
            parts.append(phone.formatted)
        elif phone.number:
            parts.append(phone.number)
    return "; ".join(parts) if parts else None


def _specializations(detail: VacancyDetail | VacancyShort) -> list[str]:
    names: list[str] = []
    for role in detail.professional_roles or []:
        if role.name and role.name not in names:
            names.append(role.name)
    if not names:
        names = ["AQA", "Python"]
    return names


def detail_to_vacancy(
    detail: VacancyDetail,
    *,
    is_duplicate: bool = False,
    duplicate_group_id: str | None = None,
    duplicate_reason: str | None = None,
) -> Vacancy:
    cleaned = html_to_text(detail.description)
    title = (detail.name or "").strip()
    body = f"{title}\n\n{cleaned}".strip() if cleaned else title
    lo, hi, avg, currency = _salary_fields(detail.salary_range)
    city = detail.area.name if detail.area else None
    if not city and detail.address and detail.address.city:
        city = detail.address.city
    urls: list[str] = []
    for u in (detail.apply_alternate_url, detail.apply_url, detail.alternate_url):
        cleaned = clean_hh_url(u)
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return Vacancy(
        id=stable_vacancy_id(detail.id),
        source_post_key=hh_source_post_key(detail.id),
        source_channel=HH_CHANNEL,
        source_url=HH_SOURCE_URL,
        post_url=clean_hh_url(detail.alternate_url),
        post_date=parse_hh_datetime(detail.published_at),
        raw_text=detail.description or body,
        cleaned_text=body,
        detected_specializations=_specializations(detail),
        detected_grade="unknown",
        detected_salary_min=lo,
        detected_salary_max=hi,
        detected_salary_avg=avg,
        salary_currency=currency,
        work_format=map_work_format(detail.work_format),
        country=None,
        city=city,
        company=detail.employer.name if detail.employer else None,
        contact=_contact_from(detail.contacts),
        technologies=[s.name for s in detail.key_skills if s.name],
        required_experience=detail.experience.name if detail.experience else None,
        employment_type=detail.employment_form.name if detail.employment_form else None,
        application_urls=urls,
        extraction_source=EXTRACTION_SOURCE,
        vacancy_score=VACANCY_SCORE,
        vacancy_substantive_score=VACANCY_SCORE,
        is_duplicate=is_duplicate,
        duplicate_group_id=duplicate_group_id,
        duplicate_reason=duplicate_reason,
    )


def short_to_vacancy(
    short: VacancyShort,
    *,
    is_duplicate: bool = False,
    duplicate_group_id: str | None = None,
    duplicate_reason: str | None = None,
) -> Vacancy:
    """Карточка из short (для дублей без detail)."""
    snippet = short.snippet
    parts = [short.name]
    if snippet:
        if snippet.requirement:
            parts.append(html_to_text(snippet.requirement))
        if snippet.responsibility:
            parts.append(html_to_text(snippet.responsibility))
    body = "\n\n".join(p for p in parts if p)
    lo, hi, avg, currency = _salary_fields(short.salary_range or short.salary)
    urls: list[str] = []
    for u in (short.apply_alternate_url, short.alternate_url):
        cleaned = clean_hh_url(u)
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return Vacancy(
        id=stable_vacancy_id(short.id),
        source_post_key=hh_source_post_key(short.id),
        source_channel=HH_CHANNEL,
        source_url=HH_SOURCE_URL,
        post_url=clean_hh_url(short.alternate_url),
        post_date=parse_hh_datetime(short.published_at),
        raw_text=body,
        cleaned_text=body,
        detected_specializations=_specializations(short),
        detected_grade="unknown",
        detected_salary_min=lo,
        detected_salary_max=hi,
        detected_salary_avg=avg,
        salary_currency=currency,
        work_format=map_work_format(short.work_format),
        country=None,
        city=short.area.name if short.area else None,
        company=short.employer.name if short.employer else None,
        contact=None,
        technologies=[],
        required_experience=short.experience.name if short.experience else None,
        employment_type=short.employment_form.name if short.employment_form else None,
        application_urls=urls,
        extraction_source=EXTRACTION_SOURCE,
        vacancy_score=VACANCY_SCORE,
        vacancy_substantive_score=VACANCY_SCORE,
        is_duplicate=is_duplicate,
        duplicate_group_id=duplicate_group_id,
        duplicate_reason=duplicate_reason,
    )
