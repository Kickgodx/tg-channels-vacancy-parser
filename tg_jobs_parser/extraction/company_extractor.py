"""Эвристическое извлечение названия компании."""

from __future__ import annotations

import re

from tg_jobs_parser.utils.text import compact_whitespace


_COMPANY_LINE = re.compile(
    r"^(?:компани|компания|company|employer|клиент|заказчик)\s*[:\-]\s*(.+)$",
    re.I | re.M,
)


def extract_company(text: str) -> str | None:
    m = _COMPANY_LINE.search(text)
    if m:
        return compact_whitespace(m.group(1))[:120] or None
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    # если строка короткая и без явных маркеров вакансии — возможно название бренда
    if 3 <= len(first_line) <= 80:
        noisy = re.search(
            r"(вакансия|hiring|remote|удален|stack|telegram|@\w+|http)",
            first_line,
            re.I,
        )
        if not noisy:
            return first_line
    return None
