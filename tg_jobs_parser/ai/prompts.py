"""Промпты для извлечения полей вакансии (RU + EN)."""

from __future__ import annotations

import json

from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.extraction.grade_extractor import GRADE_OPTIONS

_SPECIALIZATION_RULES = """
- detected_specializations — ONLY the job role(s) this post is hiring for (usually ONE primary role).
- Read the TITLE / first lines first: "Go разработчик", "Senior Backend", "QA Engineer" define the role.
- Do NOT tag extra roles from skills, tools, or infra mentioned in requirements:
  • "тестирование", "unit tests", "качество кода" in a developer post → NOT QA / Manual QA / AQA / SDET.
  • Kubernetes, Docker, CI/CD, client-go, SRE practices in a developer post → NOT DevOps unless the title is DevOps/SRE.
  • "аналитик", "архитектура", "бизнес" in prose without hiring an analyst → NOT System Analyst / Business Analyst / Analyst.
- Channel footers ("база знаний", "другие вакансии в канале", @mentions) are NOT part of the vacancy — ignore for role.
- One clear developer vacancy → list that developer role only (e.g. ["Go"]), not QA + analyst + DevOps.
- Prefer one canonical name from ALLOWED_SPECIALIZATIONS (e.g. "Go" OR "Golang", not both unless two roles are hired).
- You MAY add a new role name only if clearly stated in the vacancy (e.g. Rust) even if not in ALLOWED list."""

SYSTEM_PROMPT_V1 = """You extract structured IT job vacancy fields from Telegram posts (Russian and/or English).
Return ONE JSON object only. No markdown, no commentary outside JSON.
Rules:
- If the post is a resume, course ad, spam, or discussion without hiring — set is_job_post=false and rejection_reason briefly.
- If SEARCH_CONTEXT sets ANY_IT_SPECIALIZATION=true — do not reject an IT job only because the role/stack is outside target_specializations (e.g. PHP, Ruby, .NET are valid).
- Do NOT invent salary, company, or URLs: use null/[] when absent in text.
- Numbers in URLs (vacancy id in path, e.g. /76348/) are NOT salary — salary_min/max must be null if pay is not stated in prose.
""" + _SPECIALIZATION_RULES + """
- work_format: one of remote, hybrid, office, relocation, unknown (or null).
- detected_grade: one of """ + ", ".join(GRADE_OPTIONS) + """ (use unknown if not stated).
  Title/role grade wins (e.g. "Senior QA" → senior). Do NOT treat verb "to staff" / "staffing" as grade staff.
- salary_currency: ISO code (RUB, USD, EUR). /HOUR suffix ONLY for explicit hourly pay («₽/час», «$15–30 в час», «почасовая оплата», «р/ч»). Monthly RUB: «200 тр», «300к», «гросс», «вилка» → RUB without /HOUR (never RUB/HOUR for «тр» or «к»).
- application_urls: external apply links (hh.ru, careers pages, forms). Exclude t.me channel links unless they are the apply form.
- confidence: 0..1 how sure you are about the main fields.
- notes: optional short clarifications (ambiguity, missing data, assumptions) — always in Russian, 1–3 sentences; null if nothing useful to add.
- rejection_reason: when is_job_post=false — brief reason in Russian."""

_FEW_SHOT_USER = """Example job post:
"Senior Go Developer, remote EU, 350-450k RUB, Requirements: Go, Kubernetes, Docker, apply: https://example.com/careers/go".
(developer only — if skills exists, they should not become extra roles, they should be listed in technologies)
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
        "technologies": ["Go", "Kubernetes", "Docker"],
        "application_urls": ["https://example.com/careers/go"],
        "confidence": 0.9,
        "notes": "Компания в тексте не указана.",
    },
    ensure_ascii=False,
)

_FEW_SHOT_RESUME = """Example NOT a job:
"Ищу работу Go разработчиком, резюме в личку"
Example JSON:
""" + json.dumps(
    {
        "is_job_post": False,
        "rejection_reason": "резюме кандидата, не вакансия",
        "confidence": 0.95,
        "notes": None,
    },
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
        user_parts.insert(
            0,
            _FEW_SHOT_USER
            + "\n\n"
            + _FEW_SHOT_RESUME
            + "\n\n---\n\n",
        )

    return [
        {"role": "system", "content": SYSTEM_PROMPT_V1},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


MATCH_PROMPT_VERSION = "match-v1"

MATCH_SYSTEM_PROMPT = """You evaluate how well a candidate resume fits an IT job vacancy (Russian and/or English).
Return ONE JSON object only. No markdown, no commentary outside JSON.
Rules:
- score: integer 0-100. Calibrate: 80-100 strong fit (role + stack + grade match),
  60-79 partial fit (role matches, notable stack/grade gaps), 40-59 weak fit,
  below 40 poor fit (different role/domain).
- Judge on role, technologies, grade/seniority, work format and domain — not on salary.
- matching_skills: resume skills/experience that the vacancy requires (max 8).
- missing_skills: key vacancy requirements absent from the resume (max 8).
- explanation: 1-3 sentences in Russian — why this score, main gaps or strengths."""


def build_match_messages(*, resume_text: str, vacancy_text: str) -> list[dict[str, str]]:
    from tg_jobs_parser.ai.schema import VacancyMatchExtraction

    schema_hint = json.dumps(VacancyMatchExtraction.model_json_schema(), ensure_ascii=False)
    user = "\n".join(
        [
            "OUTPUT_JSON_SCHEMA (follow field names):",
            schema_hint,
            "",
            "RESUME:",
            resume_text,
            "",
            "VACANCY:",
            vacancy_text,
        ]
    )
    return [
        {"role": "system", "content": MATCH_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
