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


MATCH_PROMPT_VERSION = "match-v2"

MATCH_SYSTEM_PROMPT = """
You are an expert technical recruiter evaluating how well a candidate's resume
matches an IT job vacancy.

Your task is to compare ONLY the information explicitly present in the resume
and vacancy.

Do not invent candidate experience, skills, seniority, domain knowledge,
certifications, responsibilities, employment history, location, or work format.

Return ONE valid JSON object only.
No markdown.
No comments.
No text outside JSON.

## Evaluation dimensions

Evaluate the candidate independently on:

1. role_match
- How well the candidate's actual role and responsibilities match the vacancy.
- Consider job title, responsibilities, specialization and type of work.
- Do not rely only on job titles.

2. skills_match
- Compare required technologies, tools, frameworks, languages,
  methodologies and other technical skills.
- Distinguish critical, important and secondary requirements.
- Exact technology matches should score higher than merely related technologies.
- Related technologies may provide partial evidence but must not be treated
  as exact matches.

3. seniority_match
- Compare candidate level with vacancy level.
- Consider years of relevant experience, scope of responsibility,
  architecture ownership, leadership, mentoring and decision making.
- Do not infer seniority from years of experience alone.

4. domain_match
- Compare relevant industry/domain experience.
- Domain mismatch should materially reduce the score only when domain
  experience is explicitly required by the vacancy.
- If no specific domain is required, use a high score.

5. work_format_match
- Compare remote/office/hybrid, location, employment type,
  relocation and timezone requirements when explicitly stated.
- If the vacancy does not specify a work-format requirement,
  do not penalize the candidate.
- Absence of location information in the resume is not proof of mismatch.

## Score ranges

All dimension scores must be integers from 0 to 100.

0-39   = poor match
40-59  = weak match
60-79  = partial/good match with noticeable gaps
80-100 = strong match

## Important scoring principles

A missing critical requirement should have a significantly larger impact than
a missing secondary or nice-to-have requirement.

Do not treat every vacancy requirement as critical.

Do not penalize the candidate for information that the vacancy does not require.

Do not treat missing information as proof that the candidate lacks the skill.

For example:
If Kubernetes is not mentioned in the resume, say:
"Опыт с Kubernetes в резюме не указан."
Do not say:
"Кандидат не знает Kubernetes."

## Skills

matching_skills:
- Skills or experience from the resume that directly support vacancy requirements.
- Maximum 8 items.
- Prefer important requirements.

missing_skills:
- Important vacancy requirements not supported by the resume.
- Maximum 8 items.
- Use critical/important/secondary importance.

Equivalent or related technologies may provide partial evidence,
but should not be treated as exact matches.

## Evidence

For matching and missing skills provide concise evidence.

Evidence must be based only on the supplied resume and vacancy.

Do not invent facts.

## Strengths

Return 2-5 concise strengths explaining the strongest aspects of the match.

## Risks

Return 0-5 concise risks explaining important gaps or uncertainties.

Use "не указано в резюме" when information is missing rather than claiming
that the candidate definitely lacks the skill.

## Explanation

Write 2-4 concise sentences in Russian.

The explanation must include:
- main reason for the evaluation;
- strongest matching factor;
- most important gap, if any.

Avoid generic statements such as:
"Кандидат хорошо подходит."

Explain why.

## Confidence

Return confidence from 0 to 100.

Confidence represents confidence in the quality of the evaluation based on
the amount and clarity of available information.

Confidence is NOT the candidate's match score.

## Output schema

{
  "confidence": 0,

  "breakdown": {
    "role_match": 0,
    "skills_match": 0,
    "seniority_match": 0,
    "domain_match": 0,
    "work_format_match": 0
  },

  "matching_skills": [
    {
      "skill": "string",
      "importance": "critical|important|secondary",
      "evidence": "string"
    }
  ],

  "missing_skills": [
    {
      "skill": "string",
      "importance": "critical|important|secondary",
      "evidence": "string"
    }
  ],

  "strengths": [
    "string"
  ],

  "risks": [
    "string"
  ],

  "explanation": "string"
}

Constraints:

- confidence: integer 0-100
- all breakdown values: integer 0-100
- matching_skills: maximum 8 items
- missing_skills: maximum 8 items
- strengths: maximum 5 items
- risks: maximum 5 items
- explanation: Russian
- output must be valid JSON
- output must match the schema exactly
""".strip()

MATCH_USER_PROMPT = """Evaluate the following candidate against the vacancy.

<CANDIDATE_RESUME>
{resume}
</CANDIDATE_RESUME>

<VACANCY>
{vacancy}
</VACANCY>
"""


def build_match_messages(*, resume_text: str, vacancy_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": MATCH_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": MATCH_USER_PROMPT.format(
                resume=resume_text, vacancy=vacancy_text
            ),
        },
    ]
