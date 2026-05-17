"""Промпты для извлечения полей вакансии (RU + EN)."""

from __future__ import annotations

import json

from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.ai.search_context import SearchContext

SYSTEM_PROMPT_V1 = """You extract structured IT job vacancy fields from Telegram posts (Russian and/or English).
Return ONE JSON object only. No markdown, no commentary outside JSON.
Rules:
- If the post is a resume, course ad, spam, or discussion without hiring — set is_job_post=false and rejection_reason briefly.
- If SEARCH_CONTEXT sets ANY_IT_SPECIALIZATION=true — do not reject an IT job only because the role/stack is outside target_specializations (e.g. PHP, Ruby, .NET are valid).
- Do NOT invent salary, company, or URLs: use null/[] when absent in text.
- detected_specializations: canonical role names; you MAY add a new role name if clearly stated (e.g. Rust) even if not in ALLOWED list.
- work_format: one of remote, hybrid, office, relocation, unknown (or null).
- detected_grade: intern, junior, middle, senior, lead, principal, unknown.
- salary_currency: RUB, USD, EUR, etc. (uppercase) when salary is mentioned.
- application_urls: external apply links (hh.ru, careers pages, forms). Exclude t.me channel links unless they are the apply form.
- confidence: 0..1 how sure you are about the main fields."""

_FEW_SHOT_USER = """Example job post:
"Senior Go Developer, remote EU, 350-450k RUB, apply: https://example.com/careers/go"
Example JSON:
""" + json.dumps(
    {
        "is_job_post": True,
        "rejection_reason": None,
        "detected_specializations": ["Go"],
        "detected_grade": "senior",
        "salary_min": 350000,
        "salary_max": 450000,
        "salary_currency": "RUB",
        "work_format": "remote",
        "country": None,
        "city": None,
        "application_urls": ["https://example.com/careers/go"],
        "confidence": 0.9,
    },
    ensure_ascii=False,
)

_FEW_SHOT_RESUME = """Example NOT a job:
"Ищу работу Go разработчиком, резюме в личку"
Example JSON:
""" + json.dumps(
    {"is_job_post": False, "rejection_reason": "candidate resume, not hiring", "confidence": 0.95},
    ensure_ascii=False,
)


def build_messages(
    *,
    text: str,
    search_context: SearchContext,
    use_few_shot: bool,
) -> list[dict[str, str]]:
    schema_hint = json.dumps(VacancyAIExtraction.model_json_schema(), ensure_ascii=False)
    user_parts = [
        search_context.to_prompt_block(),
        "",
        "OUTPUT_JSON_SCHEMA (follow field names):",
        schema_hint,
        "",
        "POST_TEXT:",
        text,
    ]
    if use_few_shot:
        user_parts.insert(0, _FEW_SHOT_USER + "\n\n" + _FEW_SHOT_RESUME + "\n\n---\n\n")

    return [
        {"role": "system", "content": SYSTEM_PROMPT_V1},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
