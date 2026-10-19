"""Промпты для извлечения полей вакансии (RU + EN).

Версии промптов (EXTRACT / MATCH / COVER_LETTER):
- Bump при смысловом изменении system/user/few-shot (правила полей, схема,
  примеры) — иначе кэш LLM отдаст ответы от старого промпта.
- Не bump при правках комментариев в этом файле или опечатках вне текста,
  который уходит в модель.
"""

from __future__ import annotations

import json
import warnings

from tg_jobs_parser.ai.schema import VacancyAIExtraction
from tg_jobs_parser.ai.search_context import SearchContext
from tg_jobs_parser.extraction.grade_extractor import GRADE_OPTIONS

# Источник истины для ключа кэша extraction. Меняете промпт → bump.
EXTRACT_PROMPT_VERSION = "extract-v8"

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

_JOB_SEEKER_RULES = """
- is_job_post=true ONLY when an employer/recruiter is hiring (offering a job).
- Job seeker / candidate posts → is_job_post=false even if they name a role (e.g. "QA Engineer"):
  • RU: «ищу работу», «открыт к предложениям», «резюме в личку», «рассмотрю вакансии».
  • EN: "looking for a … role/job/position", "open to opportunities", "DM me for CV/resume",
    "seeking a position", "available for hire" (candidate voice).
- A role title alone does NOT make it a vacancy. Prefer false when the author is the candidate.
"""

SYSTEM_PROMPT_V1 = (
    """You extract structured IT job vacancy fields from Telegram posts (Russian and/or English).
Return ONE JSON object only. No markdown, no commentary outside JSON.
Rules:
- If the post is a resume, course ad, spam, or discussion without hiring — set is_job_post=false and rejection_reason briefly.
"""
    + _JOB_SEEKER_RULES
    + """
- If SEARCH_CONTEXT sets ANY_IT_SPECIALIZATION=true — do not reject an IT job only because the role/stack is outside target_specializations (e.g. PHP, Ruby, .NET are valid).
- Do NOT invent salary, company, or URLs: use null/[] when absent in text.
- Numbers in URLs (vacancy id in path, e.g. /76348/) are NOT salary — salary_min/max must be null if pay is not stated in prose.
"""
    + _SPECIALIZATION_RULES
    + """
- work_format: one of remote, hybrid, office, relocation, unknown (or null).
- detected_grade: one of """
    + ", ".join(GRADE_OPTIONS)
    + """.
  Title/role grade wins (e.g. "Senior QA" → senior). Do NOT treat verb "to staff" / "staffing" as grade staff.
  If no grade word is stated, derive it from REQUIRED work experience (use the lower bound of a range):
  • no experience required («без опыта», «опыт не требуется», "no experience") or less than 1 year → intern;
  • 1 to <3 years («от 1 года», «1–3 года», «2+ years») → junior;
  • 3 to <6 years («от 3 лет», «3–5 лет», "4+ years") → middle;
  • 6+ years («от 6 лет», «более 6 лет», "7+ years") → senior.
  Count only years required from the candidate — not company age, project length, or team history.
  Neither a grade word nor required experience in the text → unknown.
- salary_min/max MUST be MONTHLY amounts (except hourly — see below). Never store annual
  totals in salary_min/max. Prefer plain ISO currency (no /YEAR).
- salary_currency: ISO (RUB, USD, EUR, JPY, CAD, …) PLUS period when needed:
  • Explicit hourly («₽/час», «$25/hour», «в час», «почасовая», «/hr») → MUST be
    «USD/HOUR» or «RUB/HOUR» (keep hourly amounts as stated; do NOT convert to monthly).
  • Annual pay → ALWAYS divide by 12, plain ISO (never …/YEAR):
    – Explicit: «Annually», «per year», «/year», «в год», «годовая».
    – Western total-comp without /month: «$125k–225k», «70k–100k CAD», «26k–39k EUR»
      (k = thousands per year is the default for USD/EUR/GBP/CAD job posts).
    – Ambiguity: the SAME number can be annual junior TC or monthly senior TC
      (e.g. ~35k USD). Use TEXT period words and context, not magnitude alone.
      If the post says Annually / $Nk TC / equity package → treat as annual → /12.
    Examples: «6M–12M JPY Annually» → 500000–1000000 JPY;
    «$125k–225k + equity» → ~10417–18750 USD; «26k–39k EUR Annually» → ~2167–3250 EUR;
    «30k–47.5k USD Annually» → ~2500–3958 USD.
  • Monthly («200 тр», «300к», «гросс», «вилка», «/month», monthly USD/EUR) → plain ISO,
    never …/HOUR or …/YEAR. Do NOT divide again.
- application_urls: external apply links (hh.ru, careers pages, forms). Exclude t.me channel links unless they are the apply form.
- confidence: 0..1 how sure you are about the main fields.
- notes: optional short clarifications (ambiguity, missing data, assumptions) — always in Russian, 1–3 sentences; null if nothing useful to add.
- rejection_reason: when is_job_post=false — brief reason in Russian."""
)


def resolve_extract_prompt_version(configured: str | None = None) -> str:
    """Версия промпта extraction для кэша/логов — всегда ``EXTRACT_PROMPT_VERSION``.

    Если в config задано другое значение — warning (синхронизируйте или bump'ните
    константу при смене промпта).
    """
    cfg = (configured or "").strip()
    if cfg and cfg != EXTRACT_PROMPT_VERSION:
        warnings.warn(
            f"ai.prompt_version={cfg!r} отличается от EXTRACT_PROMPT_VERSION="
            f"{EXTRACT_PROMPT_VERSION!r}; для кэша используется константа.",
            UserWarning,
            stacklevel=2,
        )
    return EXTRACT_PROMPT_VERSION


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

_FEW_SHOT_RESUME_EN = """Example NOT a job (English job seeker — role name is NOT hiring):
"Looking for a QA Engineer role. Open to remote. DM me for CV."
Example JSON:
""" + json.dumps(
    {
        "is_job_post": False,
        "rejection_reason": "кандидат ищет работу, не вакансия работодателя",
        "confidence": 0.95,
        "notes": None,
    },
    ensure_ascii=False,
)

_FEW_SHOT_HOURLY = """Example hourly pay (keep /HOUR; do NOT convert to monthly):
"Freelance React Native engineer, $25–40 / hour, remote. Apply https://careers.example.com/rn"
Example JSON:
""" + json.dumps(
    {
        "is_job_post": True,
        "rejection_reason": None,
        "detected_specializations": ["Frontend"],
        "detected_grade": "unknown",
        "salary_min": 25,
        "salary_max": 40,
        "salary_currency": "USD/HOUR",
        "work_format": "remote",
        "application_urls": ["https://careers.example.com/rn"],
        "confidence": 0.9,
        "notes": None,
    },
    ensure_ascii=False,
)

_FEW_SHOT_ANNUAL = """Example annual pay (convert to monthly; plain ISO, no /YEAR):
"Site Reliability Engineer | 6M - 12M JPY Annually | Japan | Tokyo Office. Apply https://jobs.example.com/sre"
Example JSON:
""" + json.dumps(
    {
        "is_job_post": True,
        "rejection_reason": None,
        "detected_specializations": ["DevOps"],
        "detected_grade": "unknown",
        "salary_min": 500000,
        "salary_max": 1000000,
        "salary_currency": "JPY",
        "work_format": "office",
        "country": "Japan",
        "city": "Tokyo",
        "application_urls": ["https://jobs.example.com/sre"],
        "confidence": 0.9,
        "notes": "Зарплата в тексте годовая — сохранена как месячная (/12).",
    },
    ensure_ascii=False,
)

_FEW_SHOT_ANNUAL_USD_K = """Example western $Nk annual (convert to monthly; no Annually word needed):
"Hiring AI/frontend engineers. $125k–225k + equity, relocation to London. Apply https://jobs.example.com/ai"
Example JSON:
""" + json.dumps(
    {
        "is_job_post": True,
        "rejection_reason": None,
        "detected_specializations": ["Frontend"],
        "detected_grade": "unknown",
        "salary_min": 10417,
        "salary_max": 18750,
        "salary_currency": "USD",
        "work_format": "relocation",
        "city": "London",
        "application_urls": ["https://jobs.example.com/ai"],
        "confidence": 0.9,
        "notes": "Вилка $125k–225k годовая — сохранена как месячная (/12).",
    },
    ensure_ascii=False,
)

_FEW_SHOT_ANNUAL_EUR_LOW = """Example low-band EUR annually (still convert to monthly — do not leave 26k as-is):
"Quality Assurance Engineer II | 26k - 39k EUR Annually | Slovakia Remote. Apply https://jobs.example.com/qa"
Example JSON:
""" + json.dumps(
    {
        "is_job_post": True,
        "rejection_reason": None,
        "detected_specializations": ["QA"],
        "detected_grade": "middle",
        "salary_min": 2167,
        "salary_max": 3250,
        "salary_currency": "EUR",
        "work_format": "remote",
        "country": "Slovakia",
        "application_urls": ["https://jobs.example.com/qa"],
        "confidence": 0.9,
        "notes": "Annually → месячные суммы (/12), даже если год выглядит «как месяц» директора.",
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
            + "\n\n"
            + _FEW_SHOT_RESUME_EN
            + "\n\n"
            + _FEW_SHOT_HOURLY
            + "\n\n"
            + _FEW_SHOT_ANNUAL
            + "\n\n"
            + _FEW_SHOT_ANNUAL_USD_K
            + "\n\n"
            + _FEW_SHOT_ANNUAL_EUR_LOW
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
            "content": MATCH_USER_PROMPT.format(resume=resume_text, vacancy=vacancy_text),
        },
    ]


COVER_LETTER_PROMPT_VERSION = "cover-letter-v1"

COVER_LETTER_TONES: dict[str, str] = {
    "neutral": "neutral and professional",
    "friendly": "friendly and conversational",
    "formal": "formal and reserved",
}

COVER_LETTER_SYSTEM_PROMPT = """
You are an expert career assistant writing a cover letter (сопроводительное
письмо) for a candidate applying to an IT job vacancy.

Rules:
- Write in the SAME language as the vacancy text (usually Russian; English
  vacancy → English letter).
- Length: 120-200 words. Plain text, ready to paste into a message or form.
- No subject line, no markdown, no placeholders like [Имя] — write a finished
  text that can be sent as is. If the resume contains the candidate's name or
  contacts, you may use them; otherwise end without a signature block.
- Ground every claim in the resume: do NOT invent experience, skills,
  employers, achievements or metrics that are not in the resume.
- Reference 2-4 concrete requirements from the vacancy and map them to real
  resume facts. Explain the fit for THIS role — no generic praise.
- Do not bring up salary expectations, relocation/visa issues or weaknesses
  unless the vacancy explicitly asks for them.
- Output ONLY the letter text. No commentary, no surrounding quotes.
""".strip()

COVER_LETTER_USER_PROMPT = """Write a {tone} cover letter for this vacancy.

<CANDIDATE_RESUME>
{resume}
</CANDIDATE_RESUME>

<VACANCY>
{vacancy}
</VACANCY>
{match_block}{notes_block}"""

_COVER_LETTER_MATCH_BLOCK = """
<MATCH_CONTEXT>
A previous evaluation of this resume against this vacancy scored {score}/100.
Strongest points: {strengths}
Matching skills: {matching_skills}
{explanation}
Use this context to emphasise proven strengths; do not mention the score
or any gaps in the letter itself.
</MATCH_CONTEXT>
"""

_COVER_LETTER_NOTES_BLOCK = """
<EXTRA_NOTES>
Additional instructions from the candidate — honour them:
{notes}
</EXTRA_NOTES>
"""


def build_cover_letter_messages(
    *,
    resume_text: str,
    vacancy_text: str,
    tone: str = "neutral",
    extra_notes: str | None = None,
    match_summary: dict | None = None,
) -> list[dict[str, str]]:
    """Сообщения для генерации сопроводительного письма (ответ — обычный текст).

    ``match_summary`` — опциональный контекст из сохранённой оценки матчинга:
    ключи ``score``, ``strengths``, ``matching_skills``, ``explanation``.
    """
    match_block = ""
    if match_summary:
        skills = match_summary.get("matching_skills") or []
        explanation = (match_summary.get("explanation") or "").strip()
        match_block = _COVER_LETTER_MATCH_BLOCK.format(
            score=match_summary.get("score") or 0,
            strengths="; ".join(match_summary.get("strengths") or []) or "—",
            matching_skills=", ".join(str(s) for s in skills) or "—",
            explanation=explanation,
        )
    notes = (extra_notes or "").strip()
    notes_block = _COVER_LETTER_NOTES_BLOCK.format(notes=notes) if notes else ""
    return [
        {"role": "system", "content": COVER_LETTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": COVER_LETTER_USER_PROMPT.format(
                tone=COVER_LETTER_TONES.get(tone, COVER_LETTER_TONES["neutral"]),
                resume=resume_text,
                vacancy=vacancy_text,
                match_block=match_block,
                notes_block=notes_block,
            ),
        },
    ]
