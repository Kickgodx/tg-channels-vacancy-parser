"""Правиловая оценка вероятности что пост — вакансия."""

from __future__ import annotations

import re

from tg_jobs_parser.extraction.normalizer import normalize_for_match

_STRONG_VACANCY = re.compile(
    r"\b("
    r"вакансия|vacancy|hire|hiring|нанимаем|ищем\s+в\s+команду|открыта\s+позиция|"
    r"open\s+position|looking\s+for|we\s+are\s+looking\s+for|"
    r"требуется|ищем\s+специалист|найм|position\s*\(|"
    r"job\s+opening|работа\s+в\s+коман"
    r")\b",
    re.I,
)

_DEV_WORDS = re.compile(
    r"\b(developer|engineer|разработчик|инженер)\b",
    re.I,
)

_EXCLUDE = re.compile(
    r"\b("
    r"ищу\s+работу|ищу\s+вакансию|резюме|resume\s+cv|cv\s+attached|"
    r"курс|обучение|школа|bootcamp|ментор|подборк|репост|"
    r"#резюме|#ищуработу"
    r")\b",
    re.I,
)

_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_TG = re.compile(r"(?:@|t\.me/)([a-zA-Z][a-zA-Z0-9_]{4,})")

_SALARY_HINT = re.compile(
    r"\b("
    r"\$|€|usd|eur|rub|руб|salary|зарплат|compensation|доход|ставк|rate|"
    r"from\s+\d|от\s+\d|до\s+\d|\d+\s*-\s*\d+\s*[k$€₽]?|"
    r"\d+\s*k\b"
    r")\b",
    re.I,
)

_GRADE_HINT = re.compile(
    r"\b("
    r"intern|trainee|junior|middle|senior|staff|lead|principal|"
    r"мидл|сеньор|ведущ|стаж"
    r")\b",
    re.I,
)

_COMPANY_HINT = re.compile(
    r"\b(компани|company|employer|стартап|startup|клиент\s*:|brand|бренд)\b",
    re.I,
)


def compute_vacancy_score(raw_text: str) -> tuple[int, str, int]:
    """
    Возвращает ``(score, reason_human, substantive_score)``.

    ``substantive_score`` — сумма баллов **без** бонуса за email или @username (контакт):
    нужен, чтобы не пропускать посты только из‑за одного упоминания контакта.

    Чем выше ``score`` — тем выше уверенность что это объявление о работе работодателя.
    """
    t = normalize_for_match(raw_text)
    score = 0
    substantive = 0
    hints: list[str] = []

    if _EXCLUDE.search(t):
        return 0, "exclude_keywords", 0

    if _STRONG_VACANCY.search(raw_text.lower()):
        score += 3
        substantive += 3
        hints.append("vacancy_markers")

    if _DEV_WORDS.search(raw_text.lower()):
        score += 2
        substantive += 2
        hints.append("dev_role_words")

    if _SALARY_HINT.search(t):
        score += 2
        substantive += 2
        hints.append("salary_hint")

    if _GRADE_HINT.search(t):
        score += 1
        substantive += 1
        hints.append("grade_hint")

    if _COMPANY_HINT.search(t):
        score += 1
        substantive += 1
        hints.append("company_hint")

    if _EMAIL.search(raw_text) or _TG.search(raw_text.lower()):
        score += 2
        hints.append("contact")

    if re.search(r"\b(remote|hybrid|office|офис|удален|гибрид)\b", t):
        score += 1
        substantive += 1
        hints.append("work_format")

    reason = "+".join(hints) if hints else "baseline"
    return score, reason, substantive
